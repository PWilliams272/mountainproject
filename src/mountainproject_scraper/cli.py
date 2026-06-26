from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit

import typer

from .catalog import ExportCatalog
from .client import MountainProjectClient
from .crawler import CrawlOptions, MountainProjectCrawler
from .exporters import JsonExporter
from .hydrate import MissingRouteStatsHydrator
from .progress import create_progress_reporter, format_elapsed
from .structured_store import StructuredLocalStore

app = typer.Typer(no_args_is_help=True, help="Scrape Mountain Project areas into JSON and JSONL files.")


@app.command("scrape-area")
def scrape_area(
    start_url: str = typer.Argument(..., help="Mountain Project area URL to start from."),
    output_dir: Path | None = typer.Option(None, help="Directory for this export. Defaults to data/exports/<area-slug>."),
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into."),
    full_depth: bool = typer.Option(False, "--full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(False, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(False, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} ."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    if "/area/" not in start_url:
        raise typer.BadParameter("start_url must be a Mountain Project area URL.")
    if download_images and not resolve_photo_pages:
        raise typer.BadParameter("--download-images requires --resolve-photo-pages.")
    crawl_max_depth = None if full_depth else max_depth
    if output_dir is None:
        output_dir = catalog_root / _default_export_name(start_url)

    file_email: str | None = None
    file_password: str | None = None
    if auth_credentials_file is not None:
        payload = json.loads(auth_credentials_file.read_text(encoding="utf-8"))
        file_email = payload.get("email")
        file_password = payload.get("password")

    login_email = login_email or file_email
    login_password = login_password or file_password
    if bool(login_email) != bool(login_password):
        raise typer.BadParameter("Provide both login credentials or neither. Use env vars, --auth-credentials-file, or both.")
    if login_email and cookie:
        raise typer.BadParameter("Choose either login credentials or --cookie session auth, not both.")

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_root.mkdir(parents=True, exist_ok=True)
    started_at = perf_counter()
    reporter_holder: dict[str, object] = {"reporter": None}

    def emit(message: str) -> None:
        noisy_prefixes = (
            "Scraping area:",
            "Scraping route:",
            "Hydrated route stats ",
            "Reusing saved area:",
            "Reusing saved route:",
            "Skipping existing route stats:",
            "Skipping out-of-scope area:",
            "Skipping out-of-scope route:",
            "Skipping invalid route URL:",
            "Skipping missing route URL (404):",
        )
        if progress and message.startswith(noisy_prefixes):
            return
        reporter = reporter_holder.get("reporter")
        if progress and reporter is not None:
            reporter.log(message)
            return
        typer.echo(message)

    client = MountainProjectClient(
        delay_seconds=delay_seconds,
        cache_dir=output_dir / ".cache" / "http",
        user_agent=user_agent,
        cookies=cookie,
        login_email=login_email,
        login_password=login_password,
        cache_mode=http_cache_mode,
        log=emit,
    )
    auth_mode = "login" if login_email else ("cookie" if cookie else "anonymous")
    export_catalog = (
        ExportCatalog(root=catalog_root, current_output_dir=output_dir, auth_mode=auth_mode)
        if reuse_catalog
        else None
    )

    with create_progress_reporter(progress) as reporter:
        reporter_holder["reporter"] = reporter
        if hydrate_missing_route_stats_only:
            if not fetch_route_stats:
                raise typer.BadParameter("--hydrate-missing-route-stats-only requires --fetch-route-stats.")
            if not (output_dir / "routes.jsonl").exists():
                raise typer.BadParameter("routes.jsonl was not found in the output directory. Run a scrape first.")

            exporter = JsonExporter(output_dir, reset_jsonl=False, reuse_catalog=export_catalog)
            hydrator = MissingRouteStatsHydrator(
                client=client,
                exporter=exporter,
                workers=route_stats_workers,
                log=emit,
                progress=reporter,
            )
            manifest = hydrator.hydrate(output_dir)
            manifest["http_cache_mode"] = http_cache_mode
            manifest["auth_mode"] = auth_mode
            manifest["catalog_root"] = str(catalog_root)
            manifest["reuse_catalog"] = reuse_catalog
            if materialize_structured_storage:
                reporter.set_status("Building structured storage")
                manifest["structured_storage"] = StructuredLocalStore(output_dir).sync(manifest)
                exporter.write_manifest(manifest)
            counts = manifest["counts"]
            elapsed = format_elapsed(perf_counter() - started_at)
            typer.echo(
                "Hydrated route stats: "
                f"{counts['route_stats_routes']} route-stats bundles, "
                f"{counts['route_stars']} star ratings, "
                f"{counts['route_todos']} to-do records, "
                f"{counts['route_ticks']} ticks, "
                f"elapsed {elapsed}"
            )
            typer.echo(f"Manifest: {output_dir / 'manifest.json'}")
            return

        exporter = JsonExporter(
            output_dir,
            reset_jsonl=not resume_output,
            reuse_catalog=export_catalog,
        )
        crawler = MountainProjectCrawler(
            client=client,
            exporter=exporter,
            options=CrawlOptions(
                max_depth=crawl_max_depth,
                fetch_comments=fetch_comments,
                fetch_route_stats=fetch_route_stats,
                skip_existing_route_stats=resume_output or reuse_catalog,
                reuse_existing_data=resume_output or reuse_catalog,
                route_workers=route_workers,
                route_stats_workers=route_stats_workers,
                resolve_photo_pages=resolve_photo_pages,
                download_images=download_images,
                save_html=save_html,
            ),
            log=emit,
            progress=reporter,
        )

        manifest = crawler.crawl_area_tree(start_url)
        manifest["http_cache_mode"] = http_cache_mode
        manifest["auth_mode"] = auth_mode
        manifest["catalog_root"] = str(catalog_root)
        manifest["reuse_catalog"] = reuse_catalog
        if materialize_structured_storage:
            reporter.set_status("Building structured storage")
            manifest["structured_storage"] = StructuredLocalStore(output_dir).sync(manifest)
            exporter.write_manifest(manifest)
        counts = manifest["counts"]
        elapsed = format_elapsed(perf_counter() - started_at)
        typer.echo(
            "Finished: "
            f"{counts['areas']} areas, "
            f"{counts['routes']} routes, "
            f"{counts['comments']} comments, "
            f"{counts['photos']} photos, "
            f"{counts['route_stats_routes']} route-stats bundles, "
            f"{counts['route_ticks']} ticks, "
            f"elapsed {elapsed}"
        )
        typer.echo(f"Manifest: {output_dir / 'manifest.json'}")
        reporter_holder["reporter"] = None


def main() -> None:
    app()


def _default_export_name(start_url: str) -> str:
    path = urlsplit(start_url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else "scrape"
    slug = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    return slug or "scrape"


if __name__ == "__main__":
    main()
