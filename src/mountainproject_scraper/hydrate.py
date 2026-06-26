from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from requests import HTTPError, RequestException

from .client import MountainProjectClient
from .exporters import JsonExporter
from .models import RouteRecord
from .progress import NullProgressReporter
from .route_stats_fetcher import RouteStatsFetcher


@dataclass(slots=True)
class StoredRouteRef:
    route_id: str
    url: str
    name: str


class MissingRouteStatsHydrator:
    def __init__(
        self,
        *,
        client: MountainProjectClient,
        exporter: JsonExporter,
        workers: int,
        log: Callable[[str], None] | None = None,
        progress=None,
    ) -> None:
        self.client = client
        self.exporter = exporter
        self.workers = max(1, workers)
        self.log = log or (lambda _: None)
        self.progress = progress or NullProgressReporter()
        self.fetcher = RouteStatsFetcher(client, workers=self.workers)

    def hydrate(self, output_dir: Path) -> dict[str, object]:
        route_refs = self._load_route_refs(output_dir / "routes.jsonl")
        if not route_refs:
            raise ValueError("No routes found in routes.jsonl. Run a scrape first before hydrating route stats.")

        reused_routes: list[StoredRouteRef] = []
        missing_routes: list[StoredRouteRef] = []
        for route in route_refs:
            if self.exporter.load_route_stats(route.route_id) is not None:
                reused_routes.append(route)
            else:
                missing_routes.append(route)
        self.log(
            f"Hydrating missing route stats for {len(missing_routes)} of {len(route_refs)} existing routes"
        )
        self.progress.set_status("Hydrating route stats")
        self.progress.register_route_stats(len(route_refs))

        for route in reused_routes:
            bundle = self.exporter.load_route_stats(route.route_id)
            if bundle is None:
                continue
            self.exporter.write_route_stats(bundle)
            self.progress.complete_route_stats(route.name)
            self.log(f"Reused route stats: {route.name}")

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._fetch_bundle_for_route, route): route
                for route in missing_routes
            }
            for index, future in enumerate(as_completed(futures), start=1):
                bundle = future.result()
                route = futures[future]
                if bundle is not None:
                    self.exporter.write_route_stats(bundle)
                self.progress.complete_route_stats(route.name)
                self.log(f"Hydrated route stats {index}/{len(missing_routes)}: {route.name}")

        manifest = self._build_manifest(output_dir)
        self.exporter.write_manifest(manifest)
        return manifest

    def _fetch_bundle_for_route(self, route_ref: StoredRouteRef):
        route = RouteRecord(route_id=route_ref.route_id, url=route_ref.url, name=route_ref.name)
        try:
            return self.fetcher.fetch_for_route(route)
        except HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code is not None and 500 <= status_code < 600:
                self.exporter.write_skip_record(
                    kind="route_stats",
                    url=route.url,
                    status_code=status_code,
                    route_id=route.route_id,
                    note="Skipped route stats during hydration after persistent server error",
                )
                self.log(f"Skipping route stats during hydration after server error ({status_code}): {route.url}")
                return None
            raise
        except RequestException as exc:
            self.exporter.write_skip_record(
                kind="route_stats",
                url=route.url,
                status_code=None,
                route_id=route.route_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                note="Skipped route stats during hydration after persistent transport error",
            )
            self.log(f"Skipping route stats during hydration after transport error ({type(exc).__name__}): {route.url}")
            return None

    def _build_manifest(self, output_dir: Path) -> dict[str, object]:
        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        manifest["fetch_route_stats"] = True
        manifest["reuse_existing_data"] = True
        manifest["route_stats_workers"] = self.workers
        manifest["counts"] = dict(self.exporter.counts)
        return manifest

    def _load_route_refs(self, path: Path) -> list[StoredRouteRef]:
        route_refs: list[StoredRouteRef] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                route_id = str(payload.get("route_id") or "")
                url = str(payload.get("url") or "")
                if not route_id or not url:
                    continue
                route_refs.append(
                    StoredRouteRef(
                        route_id=route_id,
                        url=url,
                        name=str(payload.get("name") or url),
                    )
                )
        return route_refs