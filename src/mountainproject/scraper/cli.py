from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit

import typer

from ..storage.catalog import ExportCatalog
from ..storage.exporters import JsonExporter
from ..storage.structured_store import StructuredLocalStore
from .client import MountainProjectClient
from .crawler import CrawlOptions, MountainProjectCrawler
from .extract import (
    parse_international_continent_area_urls,
    parse_route_guide_international_area_url,
    parse_route_guide_state_area_urls,
)
from .hydrate import MissingRouteStatsHydrator
from .progress import create_progress_reporter, format_elapsed

app = typer.Typer(no_args_is_help=True, help="Scrape Mountain Project areas into JSON and JSONL files.")
CLI_COMMAND_NAMES = {
    "scrape-area",
    "list-state-area-urls",
    "list-continent-area-urls",
    "list-pulled-states",
    "list-pulled-continents",
    "list-unpulled-states",
    "list-unpulled-continents",
    "pull-state",
    "pull-continent",
    "pull-international",
    "pull-unpulled-states",
    "pull-unpulled-continents",
}
ROUTE_GUIDE_URL = "https://www.mountainproject.com/route-guide"
DEFAULT_AUTH_CREDENTIALS_FILE = Path("mountainproject-auth.json")


@app.command("scrape-area")
def scrape_area(
    start_url: str = typer.Argument(..., help="Mountain Project area URL to start from."),
    output_dir: Path | None = typer.Option(None, help="Directory for this export. Defaults to data/exports/<area-slug>."),
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
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

    auth_credentials_file = _resolve_auth_credentials_file(
        auth_credentials_file=auth_credentials_file,
        login_email=login_email,
        login_password=login_password,
        cookie=cookie,
    )

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


@app.command("list-state-area-urls")
def list_state_area_urls(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    only_missing: bool = typer.Option(False, "--only-missing", help="Show only states that do not already have a completed full-depth crawl manifest."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    catalog_root.mkdir(parents=True, exist_ok=True)
    client = MountainProjectClient(
        delay_seconds=0.0,
        cache_dir=catalog_root / ".cache" / "http",
        user_agent=user_agent,
        cache_mode=http_cache_mode,
    )
    route_guide = client.fetch_text(ROUTE_GUIDE_URL)
    state_area_urls = parse_route_guide_state_area_urls(route_guide.text, page_url=ROUTE_GUIDE_URL)

    catalog = ExportCatalog(root=catalog_root)
    rows: list[dict[str, object]] = []
    for state_name, area_url in state_area_urls:
        completed = catalog.find_completed_crawl(area_url, require_full_depth=True)
        if only_missing and completed is not None:
            continue
        rows.append(
            {
                "state": state_name,
                "area_url": area_url,
                "completed_full_depth": completed is not None,
                "export_name": completed.export_name if completed is not None else None,
                "finished_at": completed.manifest.get("finished_at") if completed is not None else None,
            }
        )

    if output_format == "json":
        typer.echo(json.dumps(rows, indent=2))
        return

    for row in rows:
        status = "done" if row["completed_full_depth"] else "missing"
        suffix = f" [{status}]"
        if row["export_name"]:
            suffix += f" {row['export_name']}"
        typer.echo(f"{row['state']}: {row['area_url']}{suffix}")

    typer.echo(f"Total states listed: {len(rows)}")


@app.command("list-pulled-states")
def list_pulled_states(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_state_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    _emit_state_rows([row for row in rows if row["completed_full_depth"]], output_format=output_format)


@app.command("list-unpulled-states")
def list_unpulled_states(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_state_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    _emit_state_rows([row for row in rows if not row["completed_full_depth"]], output_format=output_format)


@app.command("list-continent-area-urls")
def list_continent_area_urls(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    only_missing: bool = typer.Option(False, "--only-missing", help="Show only continents that do not already have a completed full-depth crawl manifest."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_continent_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    if only_missing:
        rows = [row for row in rows if not row["completed_full_depth"]]
    _emit_continent_rows(rows, output_format=output_format)


@app.command("list-pulled-continents")
def list_pulled_continents(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_continent_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    _emit_continent_rows([row for row in rows if row["completed_full_depth"]], output_format=output_format)


@app.command("list-unpulled-continents")
def list_unpulled_continents(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds export directories and manifests."),
    output_format: Literal["text", "json"] = typer.Option("text", "--format", help="Output format."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache the route-guide page response for this command."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_continent_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    _emit_continent_rows([row for row in rows if not row["completed_full_depth"]], output_format=output_format)


@app.command("pull-state")
def pull_state(
    state: str = typer.Argument(..., help="State name, for example 'Colorado' or 'west-virginia'."),
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    skip_if_pulled: bool = typer.Option(True, "--skip-if-pulled/--no-skip-if-pulled", help="Return immediately if the state already has a completed full-depth crawl manifest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pulled and exit without scraping."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into when --no-full-depth is used."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    state_row = _resolve_state_row(
        state,
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    if skip_if_pulled and state_row["completed_full_depth"]:
        typer.echo(f"Skipping {state_row['state']}: already pulled in {state_row['export_name']}")
        return
    if dry_run:
        typer.echo(f"Would pull {state_row['state']}: {state_row['area_url']}")
        return
    scrape_area(
        start_url=str(state_row["area_url"]),
        output_dir=None,
        catalog_root=catalog_root,
        max_depth=max_depth,
        full_depth=full_depth,
        delay_seconds=delay_seconds,
        http_cache_mode=http_cache_mode,
        fetch_comments=fetch_comments,
        fetch_route_stats=fetch_route_stats,
        hydrate_missing_route_stats_only=hydrate_missing_route_stats_only,
        route_workers=route_workers,
        route_stats_workers=route_stats_workers,
        resume_output=resume_output,
        reuse_catalog=reuse_catalog,
        progress=progress,
        materialize_structured_storage=materialize_structured_storage,
        resolve_photo_pages=resolve_photo_pages,
        download_images=download_images,
        save_html=save_html,
        auth_credentials_file=auth_credentials_file,
        login_email=login_email,
        login_password=login_password,
        cookie=cookie,
        user_agent=user_agent,
    )


@app.command("pull-continent")
def pull_continent(
    continent: str = typer.Argument(..., help="Continent name, for example 'Europe' or 'north-america'."),
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    skip_if_pulled: bool = typer.Option(True, "--skip-if-pulled/--no-skip-if-pulled", help="Return immediately if the continent already has a completed full-depth crawl manifest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pulled and exit without scraping."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into when --no-full-depth is used."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    continent_row = _resolve_continent_row(
        continent,
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    if skip_if_pulled and continent_row["completed_full_depth"]:
        typer.echo(f"Skipping {continent_row['continent']}: already pulled in {continent_row['export_name']}")
        return
    if dry_run:
        typer.echo(f"Would pull {continent_row['continent']}: {continent_row['area_url']}")
        return
    scrape_area(
        start_url=str(continent_row["area_url"]),
        output_dir=None,
        catalog_root=catalog_root,
        max_depth=max_depth,
        full_depth=full_depth,
        delay_seconds=delay_seconds,
        http_cache_mode=http_cache_mode,
        fetch_comments=fetch_comments,
        fetch_route_stats=fetch_route_stats,
        hydrate_missing_route_stats_only=hydrate_missing_route_stats_only,
        route_workers=route_workers,
        route_stats_workers=route_stats_workers,
        resume_output=resume_output,
        reuse_catalog=reuse_catalog,
        progress=progress,
        materialize_structured_storage=materialize_structured_storage,
        resolve_photo_pages=resolve_photo_pages,
        download_images=download_images,
        save_html=save_html,
        auth_credentials_file=auth_credentials_file,
        login_email=login_email,
        login_password=login_password,
        cookie=cookie,
        user_agent=user_agent,
    )


@app.command("pull-unpulled-states")
def pull_unpulled_states(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show which states would be pulled and exit without scraping."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into when --no-full-depth is used."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_state_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    missing_rows = [row for row in rows if not row["completed_full_depth"]]
    if dry_run:
        typer.echo(f"Would pull {len(missing_rows)} unpulled states.")
        for index, row in enumerate(missing_rows, start=1):
            typer.echo(f"[{index}/{len(missing_rows)}] {row['state']}: {row['area_url']}")
        return

    typer.echo(f"Pulling {len(missing_rows)} unpulled states.")
    for index, row in enumerate(missing_rows, start=1):
        typer.echo(f"[{index}/{len(missing_rows)}] Pulling {row['state']}: {row['area_url']}")
        scrape_area(
            start_url=str(row["area_url"]),
            output_dir=None,
            catalog_root=catalog_root,
            max_depth=max_depth,
            full_depth=full_depth,
            delay_seconds=delay_seconds,
            http_cache_mode=http_cache_mode,
            fetch_comments=fetch_comments,
            fetch_route_stats=fetch_route_stats,
            hydrate_missing_route_stats_only=hydrate_missing_route_stats_only,
            route_workers=route_workers,
            route_stats_workers=route_stats_workers,
            resume_output=resume_output,
            reuse_catalog=reuse_catalog,
            progress=progress,
            materialize_structured_storage=materialize_structured_storage,
            resolve_photo_pages=resolve_photo_pages,
            download_images=download_images,
            save_html=save_html,
            auth_credentials_file=auth_credentials_file,
            login_email=login_email,
            login_password=login_password,
            cookie=cookie,
            user_agent=user_agent,
        )


@app.command("pull-international")
def pull_international(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    skip_if_pulled: bool = typer.Option(True, "--skip-if-pulled/--no-skip-if-pulled", help="Skip continents that already have a completed full-depth crawl manifest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show which continents would be pulled and exit without scraping."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into when --no-full-depth is used."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    rows = _load_continent_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    target_rows = [row for row in rows if not row["completed_full_depth"]] if skip_if_pulled else rows
    if dry_run:
        typer.echo(f"Would pull {len(target_rows)} continents from International.")
        for index, row in enumerate(target_rows, start=1):
            typer.echo(f"[{index}/{len(target_rows)}] {row['continent']}: {row['area_url']}")
        return

    typer.echo(f"Pulling {len(target_rows)} continents from International.")
    for index, row in enumerate(target_rows, start=1):
        typer.echo(f"[{index}/{len(target_rows)}] Pulling {row['continent']}: {row['area_url']}")
        scrape_area(
            start_url=str(row["area_url"]),
            output_dir=None,
            catalog_root=catalog_root,
            max_depth=max_depth,
            full_depth=full_depth,
            delay_seconds=delay_seconds,
            http_cache_mode=http_cache_mode,
            fetch_comments=fetch_comments,
            fetch_route_stats=fetch_route_stats,
            hydrate_missing_route_stats_only=hydrate_missing_route_stats_only,
            route_workers=route_workers,
            route_stats_workers=route_stats_workers,
            resume_output=resume_output,
            reuse_catalog=reuse_catalog,
            progress=progress,
            materialize_structured_storage=materialize_structured_storage,
            resolve_photo_pages=resolve_photo_pages,
            download_images=download_images,
            save_html=save_html,
            auth_credentials_file=auth_credentials_file,
            login_email=login_email,
            login_password=login_password,
            cookie=cookie,
            user_agent=user_agent,
        )


@app.command("pull-unpulled-continents")
def pull_unpulled_continents(
    catalog_root: Path = typer.Option(Path("data/exports"), help="Top-level directory that holds all reusable export directories."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show which continents would be pulled and exit without scraping."),
    max_depth: int = typer.Option(0, min=0, help="How many child-area levels to recurse into when --no-full-depth is used."),
    full_depth: bool = typer.Option(True, "--full-depth/--no-full-depth", help="Recurse through all in-scope descendant areas until no child areas remain."),
    delay_seconds: float = typer.Option(0.1, min=0.0, help="Global minimum delay between uncached requests across all worker threads."),
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"] = typer.Option("ephemeral", "--http-cache-mode", help="How to cache full HTTP response bodies during a scrape."),
    fetch_comments: bool = typer.Option(True, "--fetch-comments/--no-fetch-comments", help="Fetch route and area comments via the comments fragment endpoint."),
    fetch_route_stats: bool = typer.Option(True, "--fetch-route-stats/--no-fetch-route-stats", help="Fetch route stars, suggested ratings, to-do users, and ticks via the route stats API."),
    hydrate_missing_route_stats_only: bool = typer.Option(False, "--hydrate-missing-route-stats-only", help="Fetch route stats only for routes already present in routes.jsonl that do not yet have route stats exported."),
    route_workers: int = typer.Option(8, min=1, help="Worker threads to use when scraping route pages within an area."),
    route_stats_workers: int = typer.Option(8, min=1, help="Worker threads to use when fetching route stats endpoints."),
    resume_output: bool = typer.Option(True, "--resume-output/--fresh-output", help="Preserve existing JSONL exports and append only missing records instead of truncating the output directory."),
    reuse_catalog: bool = typer.Option(True, "--reuse-catalog/--no-reuse-catalog", help="Reuse saved areas, routes, and route stats from sibling exports in the catalog root when available."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show Rich progress bars and an elapsed timer while scraping."),
    materialize_structured_storage: bool = typer.Option(True, "--materialize-structured-storage/--no-materialize-structured-storage", help="Build local Parquet tables and a DuckDB database from the exported JSONL tables."),
    resolve_photo_pages: bool = typer.Option(False, "--resolve-photo-pages/--no-resolve-photo-pages", help="Fetch each photo page to resolve direct image URLs."),
    download_images: bool = typer.Option(False, "--download-images/--no-download-images", help="Download resolved image files into the output directory."),
    save_html: bool = typer.Option(False, "--save-html/--no-save-html", help="Persist raw HTML snapshots for debugging and re-parsing."),
    auth_credentials_file: Path | None = typer.Option(None, "--auth-credentials-file", help="Path to a JSON file with Mountain Project login credentials: {'email': '...', 'password': '...'} . Defaults to ./mountainproject-auth.json when present."),
    login_email: str | None = typer.Option(None, "--login-email", envvar="MOUNTAINPROJECT_EMAIL", help="Mountain Project login email. Prefer the MOUNTAINPROJECT_EMAIL env var."),
    login_password: str | None = typer.Option(None, "--login-password", envvar="MOUNTAINPROJECT_PASSWORD", help="Mountain Project login password. Prefer the MOUNTAINPROJECT_PASSWORD env var or --auth-credentials-file."),
    cookie: list[str] | None = typer.Option(None, "--cookie", help="Repeatable session cookie in name=value form."),
    user_agent: str = typer.Option("Mozilla/5.0", help="User-Agent header to send with requests."),
) -> None:
    pull_international(
        catalog_root=catalog_root,
        skip_if_pulled=True,
        dry_run=dry_run,
        max_depth=max_depth,
        full_depth=full_depth,
        delay_seconds=delay_seconds,
        http_cache_mode=http_cache_mode,
        fetch_comments=fetch_comments,
        fetch_route_stats=fetch_route_stats,
        hydrate_missing_route_stats_only=hydrate_missing_route_stats_only,
        route_workers=route_workers,
        route_stats_workers=route_stats_workers,
        resume_output=resume_output,
        reuse_catalog=reuse_catalog,
        progress=progress,
        materialize_structured_storage=materialize_structured_storage,
        resolve_photo_pages=resolve_photo_pages,
        download_images=download_images,
        save_html=save_html,
        auth_credentials_file=auth_credentials_file,
        login_email=login_email,
        login_password=login_password,
        cookie=cookie,
        user_agent=user_agent,
    )


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] not in CLI_COMMAND_NAMES and not argv[0].startswith("-"):
        app(args=["scrape-area", *argv])
        return
    app()


def _default_export_name(start_url: str) -> str:
    path = urlsplit(start_url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else "scrape"
    slug = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    return slug or "scrape"


def _load_state_rows(
    *,
    catalog_root: Path,
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"],
    user_agent: str,
) -> list[dict[str, object]]:
    catalog_root.mkdir(parents=True, exist_ok=True)
    client = MountainProjectClient(
        delay_seconds=0.0,
        cache_dir=catalog_root / ".cache" / "http",
        user_agent=user_agent,
        cache_mode=http_cache_mode,
    )
    route_guide = client.fetch_text(ROUTE_GUIDE_URL)
    state_area_urls = parse_route_guide_state_area_urls(route_guide.text, page_url=ROUTE_GUIDE_URL)

    return _build_named_area_rows("state", state_area_urls, catalog_root=catalog_root)


def _load_continent_rows(
    *,
    catalog_root: Path,
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"],
    user_agent: str,
) -> list[dict[str, object]]:
    catalog_root.mkdir(parents=True, exist_ok=True)
    client = MountainProjectClient(
        delay_seconds=0.0,
        cache_dir=catalog_root / ".cache" / "http",
        user_agent=user_agent,
        cache_mode=http_cache_mode,
    )
    international_area_url = _resolve_international_area_url(client)
    international_page = client.fetch_text(international_area_url)
    continent_area_urls = parse_international_continent_area_urls(
        international_page.text,
        page_url=international_area_url,
    )

    return _build_named_area_rows("continent", continent_area_urls, catalog_root=catalog_root)


def _build_named_area_rows(
    name_key: str,
    named_area_urls: list[tuple[str, str]],
    *,
    catalog_root: Path,
) -> list[dict[str, object]]:

    catalog = ExportCatalog(root=catalog_root)
    rows: list[dict[str, object]] = []
    for area_name, area_url in named_area_urls:
        completed = catalog.find_completed_crawl(area_url, require_full_depth=True)
        rows.append(
            {
                name_key: area_name,
                "area_url": area_url,
                "completed_full_depth": completed is not None,
                "export_name": completed.export_name if completed is not None else None,
                "finished_at": completed.manifest.get("finished_at") if completed is not None else None,
            }
        )
    return rows


def _emit_state_rows(rows: list[dict[str, object]], *, output_format: Literal["text", "json"]) -> None:
    if output_format == "json":
        typer.echo(json.dumps(rows, indent=2))
        return

    for row in rows:
        status = "done" if row["completed_full_depth"] else "missing"
        suffix = f" [{status}]"
        if row["export_name"]:
            suffix += f" {row['export_name']}"
        typer.echo(f"{row['state']}: {row['area_url']}{suffix}")
    typer.echo(f"Total states listed: {len(rows)}")


def _emit_continent_rows(rows: list[dict[str, object]], *, output_format: Literal["text", "json"]) -> None:
    if output_format == "json":
        typer.echo(json.dumps(rows, indent=2))
        return

    for row in rows:
        status = "done" if row["completed_full_depth"] else "missing"
        suffix = f" [{status}]"
        if row["export_name"]:
            suffix += f" {row['export_name']}"
        typer.echo(f"{row['continent']}: {row['area_url']}{suffix}")
    typer.echo(f"Total continents listed: {len(rows)}")


def _normalize_lookup_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _resolve_state_row(
    state: str,
    *,
    catalog_root: Path,
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"],
    user_agent: str,
) -> dict[str, object]:
    state_key = _normalize_lookup_name(state)
    rows = _load_state_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    for row in rows:
        if _normalize_lookup_name(str(row["state"])) == state_key:
            return row
    available = ", ".join(str(row["state"]) for row in rows)
    raise typer.BadParameter(f"Unknown state '{state}'. Available states: {available}")


def _resolve_continent_row(
    continent: str,
    *,
    catalog_root: Path,
    http_cache_mode: Literal["persistent", "ephemeral", "disabled"],
    user_agent: str,
) -> dict[str, object]:
    continent_key = _normalize_lookup_name(continent)
    rows = _load_continent_rows(
        catalog_root=catalog_root,
        http_cache_mode=http_cache_mode,
        user_agent=user_agent,
    )
    for row in rows:
        if _normalize_lookup_name(str(row["continent"])) == continent_key:
            return row
    available = ", ".join(str(row["continent"]) for row in rows)
    raise typer.BadParameter(f"Unknown continent '{continent}'. Available continents: {available}")


def _resolve_international_area_url(client: MountainProjectClient) -> str:
    route_guide = client.fetch_text(ROUTE_GUIDE_URL)
    international_area_url = parse_route_guide_international_area_url(
        route_guide.text,
        page_url=ROUTE_GUIDE_URL,
    )
    if international_area_url is None:
        raise RuntimeError("Could not locate the International area URL on the route-guide page.")
    return international_area_url


def _resolve_auth_credentials_file(
    *,
    auth_credentials_file: Path | None,
    login_email: str | None,
    login_password: str | None,
    cookie: list[str] | None,
) -> Path | None:
    if auth_credentials_file is not None:
        return auth_credentials_file
    if login_email or login_password or cookie:
        return None
    if DEFAULT_AUTH_CREDENTIALS_FILE.exists():
        return DEFAULT_AUTH_CREDENTIALS_FILE
    return None


if __name__ == "__main__":
    main()
