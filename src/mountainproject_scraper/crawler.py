from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
from typing import Callable
from urllib.parse import urlparse

from requests import HTTPError, RequestException

from .client import MountainProjectClient
from .exporters import JsonExporter
from .extract import (
    canonical_page_url,
    is_route_page_url,
    parse_area_page,
    parse_comments_fragment,
    parse_photo_page,
    parse_route_page,
)
from .models import AreaRecord, PhotoRecord, RouteRecord
from .progress import NullProgressReporter
from .route_stats_fetcher import RouteStatsFetcher


@dataclass(slots=True)
class CrawlOptions:
    max_depth: int | None = 0
    fetch_comments: bool = True
    fetch_route_stats: bool = False
    skip_existing_route_stats: bool = False
    reuse_existing_data: bool = False
    route_workers: int = 8
    route_stats_workers: int = 8
    resolve_photo_pages: bool = False
    download_images: bool = False
    save_html: bool = False


class MountainProjectCrawler:
    def __init__(
        self,
        *,
        client: MountainProjectClient,
        exporter: JsonExporter,
        options: CrawlOptions,
        log: Callable[[str], None] | None = None,
        progress=None,
    ) -> None:
        self.client = client
        self.exporter = exporter
        self.options = options
        self.log = log or (lambda _: None)
        self.progress = progress or NullProgressReporter()
        self._seen_areas: set[str] = set()
        self._visited_areas: set[str] = set()
        self._visited_routes: set[str] = set()
        self._resolved_photos: dict[str, PhotoRecord] = {}
        self._downloaded_photos: dict[str, str] = {}
        self._area_breadcrumb_prefix: list[str] | None = None
        self._area_breadcrumb_url_prefix: list[str] | None = None
        self._route_breadcrumb_prefix: list[str] | None = None
        self._route_breadcrumb_url_prefix: list[str] | None = None
        self._photo_lock = threading.Lock()
        self._thread_local = threading.local()
        self._route_stats_fetcher = RouteStatsFetcher(
            client,
            workers=max(1, options.route_stats_workers),
        )

    def crawl_area_tree(self, start_area_url: str) -> dict[str, object]:
        start_time = datetime.now(timezone.utc)
        root_area_url = canonical_page_url(start_area_url)
        self._seen_areas.add(root_area_url)
        queue: deque[tuple[str, int]] = deque([(root_area_url, 0)])
        self.progress.set_status("Scraping areas")
        self.progress.register_areas(1)

        while queue:
            area_url, depth = queue.popleft()
            if area_url in self._visited_areas:
                continue
            area = self._scrape_area(area_url)
            if self._area_breadcrumb_prefix is None:
                self._area_breadcrumb_prefix = area.breadcrumbs + [self._normalize_area_name(area.name)]
                self._area_breadcrumb_url_prefix = area.breadcrumb_urls + [canonical_page_url(area.url)]
            if self._route_breadcrumb_prefix is None:
                self._route_breadcrumb_prefix = area.breadcrumbs + [self._normalize_area_name(area.name)]
                self._route_breadcrumb_url_prefix = area.breadcrumb_urls + [canonical_page_url(area.url)]
            if not self._area_is_in_scope(area):
                self.log(f"Skipping out-of-scope area: {area.url}")
                self._visited_areas.add(area.url)
                self.progress.complete_area(area.name)
                continue
            self.exporter.write_area(area)
            self._visited_areas.add(area.url)

            pending_route_urls: list[str] = []
            for route_url in area.route_urls:
                if not is_route_page_url(route_url):
                    self.log(f"Skipping invalid route URL: {route_url}")
                    continue
                if route_url in self._visited_routes:
                    continue
                self._visited_routes.add(route_url)
                pending_route_urls.append(route_url)

            self.progress.complete_area(area.name)
            self.progress.register_routes(len(pending_route_urls))

            for route, bundle in self._scrape_routes(pending_route_urls):
                if not self._route_is_in_scope(route):
                    self.log(f"Skipping out-of-scope route: {route.url}")
                    continue
                self.exporter.write_route(route)
                if bundle is not None:
                    self.exporter.write_route_stats(bundle)

            if self.options.max_depth is not None and depth >= self.options.max_depth:
                continue
            new_child_area_urls: list[str] = []
            for child_area_url in area.child_area_urls:
                if child_area_url in self._seen_areas:
                    continue
                self._seen_areas.add(child_area_url)
                queue.append((child_area_url, depth + 1))
                new_child_area_urls.append(child_area_url)
            self.progress.register_areas(len(new_child_area_urls))

        finished_at = datetime.now(timezone.utc)
        manifest = {
            "start_url": canonical_page_url(start_area_url),
            "started_at": start_time.isoformat(),
            "finished_at": finished_at.isoformat(),
            "max_depth": self.options.max_depth,
            "full_depth": self.options.max_depth is None,
            "fetch_comments": self.options.fetch_comments,
            "fetch_route_stats": self.options.fetch_route_stats,
            "reuse_existing_data": self.options.reuse_existing_data,
            "route_workers": self.options.route_workers,
            "route_stats_workers": self.options.route_stats_workers,
            "resolve_photo_pages": self.options.resolve_photo_pages,
            "download_images": self.options.download_images,
            "save_html": self.options.save_html,
            "counts": self.exporter.counts,
        }
        self.exporter.write_manifest(manifest)
        return manifest

    def _scrape_routes(self, route_urls: list[str]) -> list[tuple[RouteRecord, object | None]]:
        if not route_urls:
            return []
        if self.options.route_workers <= 1:
            results: list[tuple[RouteRecord, object | None]] = []
            for route_url in route_urls:
                result = self._scrape_route_job(route_url)
                self.progress.complete_route(self._route_progress_name(route_url, result))
                if result is not None:
                    results.append(result)
            return results

        results: list[tuple[RouteRecord, object | None]] = []
        with ThreadPoolExecutor(max_workers=self.options.route_workers) as executor:
            futures = [executor.submit(self._scrape_route_job, route_url) for route_url in route_urls]
            future_to_url = {future: route_url for future, route_url in zip(futures, route_urls, strict=False)}
            for future in as_completed(future_to_url):
                result = future.result()
                self.progress.complete_route(self._route_progress_name(future_to_url[future], result))
                if result is not None:
                    results.append(result)
        return results

    def _scrape_route_job(self, route_url: str) -> tuple[RouteRecord, object | None] | None:
        client = self._client_for_current_thread()
        try:
            route = self._scrape_route(route_url, client=client)
        except HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                self.log(f"Skipping missing route URL (404): {route_url}")
                return None
            if status_code is not None and 500 <= status_code < 600:
                self.exporter.write_skip_record(
                    kind="route_page",
                    url=route_url,
                    status_code=status_code,
                    note="Skipped route page after persistent server error",
                )
                self.log(f"Skipping route URL after server error ({status_code}): {route_url}")
                return None
            raise
        except RequestException as exc:
            self.exporter.write_skip_record(
                kind="route_page",
                url=route_url,
                status_code=None,
                error_type=type(exc).__name__,
                error_message=str(exc),
                note="Skipped route page after persistent transport error",
            )
            self.log(f"Skipping route URL after transport error ({type(exc).__name__}): {route_url}")
            return None
        if not self._route_is_in_scope(route):
            return route, None
        if not self.options.fetch_route_stats:
            return route, None
        if self.options.skip_existing_route_stats:
            existing_bundle = self.exporter.load_route_stats(route.route_id)
            if existing_bundle is not None:
                self.log(f"Reusing saved route stats: {route.url}")
                return route, existing_bundle
        try:
            bundle = self._fetch_route_stats(route)
        except HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code is not None and 500 <= status_code < 600:
                self.exporter.write_skip_record(
                    kind="route_stats",
                    url=route.url,
                    status_code=status_code,
                    route_id=route.route_id,
                    note="Skipped route stats after persistent server error",
                )
                self.log(f"Skipping route stats after server error ({status_code}): {route.url}")
                return route, None
            raise
        except RequestException as exc:
            self.exporter.write_skip_record(
                kind="route_stats",
                url=route.url,
                status_code=None,
                route_id=route.route_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                note="Skipped route stats after persistent transport error",
            )
            self.log(f"Skipping route stats after transport error ({type(exc).__name__}): {route.url}")
            return route, None
        return route, bundle

    def _scrape_area(self, area_url: str, *, client: MountainProjectClient | None = None) -> AreaRecord:
        client = client or self.client
        if self.options.reuse_existing_data:
            existing = self.exporter.load_area_by_url(area_url)
            if existing is not None:
                self.log(f"Reusing saved area: {area_url}")
                return existing
        self.log(f"Scraping area: {area_url}")
        response = client.fetch_text(area_url)
        area = parse_area_page(response.text, response.url)
        if self.options.save_html:
            self.exporter.write_raw_html("areas", area.area_id, response.text)
        if self.options.fetch_comments:
            area.comments, area.comments_truncated, area.remaining_comment_count = self._fetch_comments(
                client=client,
                parent_type="area",
                parent_id=area.area_id,
                parent_url=area.url,
            )
            if area.comment_count is None:
                area.comment_count = len(area.comments) + (area.remaining_comment_count or 0)
        area.photos = self._enrich_photos(area.photos, client=client)
        return area

    def _scrape_route(self, route_url: str, *, client: MountainProjectClient | None = None) -> RouteRecord:
        client = client or self.client
        if self.options.reuse_existing_data:
            existing = self.exporter.load_route_by_url(route_url)
            if existing is not None:
                self.log(f"Reusing saved route: {route_url}")
                return existing
        self.log(f"Scraping route: {route_url}")
        response = client.fetch_text(route_url)
        route = parse_route_page(response.text, response.url)
        if self.options.save_html:
            self.exporter.write_raw_html("routes", route.route_id, response.text)
        if self.options.fetch_comments:
            route.comments, route.comments_truncated, route.remaining_comment_count = self._fetch_comments(
                client=client,
                parent_type="route",
                parent_id=route.route_id,
                parent_url=route.url,
            )
            if route.comment_count is None:
                route.comment_count = len(route.comments) + (route.remaining_comment_count or 0)
        route.photos = self._enrich_photos(route.photos, client=client)
        return route

    def _fetch_comments(
        self,
        *,
        client: MountainProjectClient,
        parent_type: str,
        parent_id: str,
        parent_url: str,
    ) -> tuple[list, bool, int | None]:
        object_type = self._comment_object_type(parent_type)
        url = f"https://www.mountainproject.com/comments/forObject/{object_type}/{parent_id}"
        response = client.fetch_text(url, params={"sortOrder": "oldest", "showAll": "true"})
        if self.options.save_html:
            self.exporter.write_raw_html("comments", f"{parent_type}-{parent_id}", response.text)
        return parse_comments_fragment(
            response.text,
            parent_type=parent_type,
            parent_id=parent_id,
            parent_url=parent_url,
        )

    def _fetch_route_stats(self, route: RouteRecord):
        return self._route_stats_fetcher.fetch_for_route(route)

    def _enrich_photos(
        self,
        photos: list[PhotoRecord],
        *,
        client: MountainProjectClient,
    ) -> list[PhotoRecord]:
        enriched: list[PhotoRecord] = []
        for photo in photos:
            photo = self._resolve_photo(photo, client=client)
            photo = self._download_photo(photo, client=client)
            enriched.append(photo)
        return enriched

    def _resolve_photo(self, photo: PhotoRecord, *, client: MountainProjectClient) -> PhotoRecord:
        if not self.options.resolve_photo_pages:
            return photo
        with self._photo_lock:
            cached = self._resolved_photos.get(photo.photo_id)
        if cached is None:
            response = client.fetch_text(photo.photo_page_url)
            if self.options.save_html:
                self.exporter.write_raw_html("photos", photo.photo_id, response.text)
            cached = parse_photo_page(response.text, photo)
            with self._photo_lock:
                self._resolved_photos[photo.photo_id] = replace(cached)
        if cached.title and not photo.title:
            photo.title = cached.title
        if cached.thumbnail_url and not photo.thumbnail_url:
            photo.thumbnail_url = cached.thumbnail_url
        if cached.image_url and not photo.image_url:
            photo.image_url = cached.image_url
        return photo

    def _download_photo(self, photo: PhotoRecord, *, client: MountainProjectClient) -> PhotoRecord:
        if not self.options.download_images:
            return photo
        source_url = photo.image_url or photo.thumbnail_url
        if not source_url:
            return photo
        with self._photo_lock:
            cached_local_path = self._downloaded_photos.get(photo.photo_id)
        if cached_local_path is not None:
            photo.local_path = cached_local_path
            return photo

        suffix = Path(urlparse(source_url).path).suffix or ".jpg"
        destination = self.exporter.images_dir / f"{photo.photo_id}{suffix}"
        client.download_file(source_url, destination)
        relative_path = str(destination.relative_to(self.exporter.output_dir))
        photo.local_path = relative_path
        with self._photo_lock:
            self._downloaded_photos[photo.photo_id] = relative_path
        return photo

    def _client_for_current_thread(self) -> MountainProjectClient:
        client = getattr(self._thread_local, "client", None)
        if client is None:
            client = self.client.clone()
            self._thread_local.client = client
        return client

    def _comment_object_type(self, parent_type: str) -> str:
        if parent_type == "area":
            return "Climb-Lib-Models-Area"
        if parent_type == "route":
            return "Climb-Lib-Models-Route"
        raise ValueError(f"Unsupported comment parent type: {parent_type}")

    def _normalize_area_name(self, name: str) -> str:
        return re.sub(r"\s+Rock Climbing$", "", name).strip()

    def _route_is_in_scope(self, route: RouteRecord) -> bool:
        if self._route_breadcrumb_url_prefix is not None and route.breadcrumb_urls:
            if len(route.breadcrumb_urls) < len(self._route_breadcrumb_url_prefix):
                return False
            return (
                route.breadcrumb_urls[: len(self._route_breadcrumb_url_prefix)]
                == self._route_breadcrumb_url_prefix
            )

        if self._route_breadcrumb_prefix is None:
            return True
        if len(route.breadcrumbs) < len(self._route_breadcrumb_prefix):
            return False
        return route.breadcrumbs[: len(self._route_breadcrumb_prefix)] == self._route_breadcrumb_prefix

    def _area_is_in_scope(self, area: AreaRecord) -> bool:
        if self._area_breadcrumb_url_prefix is not None:
            candidate_urls = area.breadcrumb_urls + [canonical_page_url(area.url)]
            if len(candidate_urls) < len(self._area_breadcrumb_url_prefix):
                return False
            return candidate_urls[: len(self._area_breadcrumb_url_prefix)] == self._area_breadcrumb_url_prefix

        if self._area_breadcrumb_prefix is None:
            return True
        candidate_names = area.breadcrumbs + [self._normalize_area_name(area.name)]
        if len(candidate_names) < len(self._area_breadcrumb_prefix):
            return False
        return candidate_names[: len(self._area_breadcrumb_prefix)] == self._area_breadcrumb_prefix

    def _route_progress_name(
        self,
        route_url: str,
        result: tuple[RouteRecord, object | None] | None,
    ) -> str:
        if result is not None:
            route, _ = result
            return route.name
        return route_url.rstrip("/").rsplit("/", 1)[-1]
