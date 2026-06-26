from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import ExportCatalog
from .models import (
    AreaRecord,
    CommentRecord,
    PhotoRecord,
    RouteRecord,
    RouteStarRecord,
    RouteStatsBundle,
    RouteSuggestedRatingRecord,
    RouteTickRecord,
    RouteTodoRecord,
    area_from_dict,
    route_from_dict,
    to_dict,
)


class JsonExporter:
    def __init__(
        self,
        output_dir: Path,
        *,
        reset_jsonl: bool = True,
        reuse_catalog: ExportCatalog | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.areas_dir = output_dir / "areas"
        self.routes_dir = output_dir / "routes"
        self.route_stats_dir = output_dir / "route_stats"
        self.raw_dir = output_dir / "raw"
        self.images_dir = output_dir / "images" / "photos"
        self.skipped_requests_path = output_dir / "skipped_requests.jsonl"
        self.reuse_catalog = reuse_catalog
        self._write_lock = threading.Lock()
        self._area_ids: set[str] = set()
        self._route_ids: set[str] = set()
        self._route_stats_route_ids: set[str] = set()
        self._area_url_to_id: dict[str, str] = {}
        self._route_url_to_id: dict[str, str] = {}
        self._comment_keys: set[tuple[str, str, str]] = set()
        self._photo_keys: set[tuple[str, str, str]] = set()
        self._route_star_keys: set[str] = set()
        self._route_rating_keys: set[str] = set()
        self._route_todo_keys: set[str] = set()
        self._route_tick_keys: set[str] = set()
        self.counts = {
            "areas": 0,
            "routes": 0,
            "comments": 0,
            "photos": 0,
            "route_stats_routes": 0,
            "route_stars": 0,
            "route_suggested_ratings": 0,
            "route_todos": 0,
            "route_ticks": 0,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.areas_dir.mkdir(parents=True, exist_ok=True)
        self.routes_dir.mkdir(parents=True, exist_ok=True)
        self.route_stats_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_files = (
            "areas.jsonl",
            "routes.jsonl",
            "comments.jsonl",
            "photos.jsonl",
            "route_stats_summary.jsonl",
            "route_stars.jsonl",
            "route_suggested_ratings.jsonl",
            "route_todos.jsonl",
            "route_ticks.jsonl",
        )

        if reset_jsonl:
            for file_name in self._jsonl_files:
                (self.output_dir / file_name).write_text("", encoding="utf-8")
            self.skipped_requests_path.write_text("", encoding="utf-8")
        else:
            self._load_existing_state()

    def write_area(self, area: AreaRecord) -> None:
        data = to_dict(area)
        data["photo_count"] = len(area.photos)
        data["route_count"] = len(area.route_urls)
        data["comment_count"] = area.comment_count if area.comment_count is not None else len(area.comments)
        self._write_json(self.areas_dir / f"{area.area_id}.json", data)
        if area.area_id not in self._area_ids:
            self._append_jsonl(self.output_dir / "areas.jsonl", data)
            self._area_ids.add(area.area_id)
            self._area_url_to_id[area.url] = area.area_id
            self.counts["areas"] += 1
        self._write_comments(area.comments)
        self._write_photos(area.photos)

    def write_route(self, route: RouteRecord) -> None:
        data = to_dict(route)
        data["photo_count"] = len(route.photos)
        data["comment_count"] = route.comment_count if route.comment_count is not None else len(route.comments)
        self._write_json(self.routes_dir / f"{route.route_id}.json", data)
        if route.route_id not in self._route_ids:
            self._append_jsonl(self.output_dir / "routes.jsonl", data)
            self._route_ids.add(route.route_id)
            self._route_url_to_id[route.url] = route.route_id
            self.counts["routes"] += 1
        self._write_comments(route.comments)
        self._write_photos(route.photos)

    def write_route_stats(self, bundle: RouteStatsBundle) -> None:
        self._write_json(self.route_stats_dir / f"{bundle.summary.route_id}.json", to_dict(bundle))
        if bundle.summary.route_id not in self._route_stats_route_ids:
            self._append_jsonl(self.output_dir / "route_stats_summary.jsonl", to_dict(bundle.summary))
            self._route_stats_route_ids.add(bundle.summary.route_id)
            self.counts["route_stats_routes"] += 1

        self._write_route_star_records(bundle.stars)
        self._write_route_rating_records(bundle.suggested_ratings)
        self._write_route_todo_records(bundle.todos)
        self._write_route_tick_records(bundle.ticks)

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        self._write_json(self.output_dir / "manifest.json", manifest)

    def write_skip_record(
        self,
        *,
        kind: str,
        url: str,
        status_code: int | None,
        route_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        note: str | None = None,
    ) -> None:
        payload = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "url": url,
            "status_code": status_code,
            "route_id": route_id,
            "error_type": error_type,
            "error_message": error_message,
            "note": note,
        }
        self._append_jsonl(self.skipped_requests_path, payload)

    def existing_route_stats_route_ids(self) -> set[str]:
        return set(self._route_stats_route_ids)

    def has_route_stats(self, route_id: str) -> bool:
        route_id = str(route_id)
        return route_id in self._route_stats_route_ids or (
            self.reuse_catalog is not None and self.reuse_catalog.has_route_stats(route_id)
        )

    def has_area(self, area_url: str) -> bool:
        return area_url in self._area_url_to_id or (
            self.reuse_catalog is not None and self.reuse_catalog.load_area_by_url(area_url) is not None
        )

    def has_route(self, route_url: str) -> bool:
        return route_url in self._route_url_to_id or (
            self.reuse_catalog is not None and self.reuse_catalog.load_route_by_url(route_url) is not None
        )

    def load_area_by_url(self, area_url: str) -> AreaRecord | None:
        area_id = self._area_url_to_id.get(area_url)
        if area_id:
            path = self.areas_dir / f"{area_id}.json"
            if path.exists():
                return area_from_dict(json.loads(path.read_text(encoding="utf-8")))
        if self.reuse_catalog is None:
            return None
        return self.reuse_catalog.load_area_by_url(area_url)

    def load_route_by_url(self, route_url: str) -> RouteRecord | None:
        route_id = self._route_url_to_id.get(route_url)
        if route_id:
            path = self.routes_dir / f"{route_id}.json"
            if path.exists():
                return route_from_dict(json.loads(path.read_text(encoding="utf-8")))
        if self.reuse_catalog is None:
            return None
        return self.reuse_catalog.load_route_by_url(route_url)

    def load_route_stats(self, route_id: str) -> RouteStatsBundle | None:
        route_id = str(route_id)
        path = self.route_stats_dir / f"{route_id}.json"
        if path.exists():
            from .catalog import route_stats_bundle_from_dict

            return route_stats_bundle_from_dict(json.loads(path.read_text(encoding="utf-8")))
        if self.reuse_catalog is None:
            return None
        return self.reuse_catalog.load_route_stats(route_id)

    def write_raw_html(self, bucket: str, name: str, html: str) -> None:
        bucket_dir = self.raw_dir / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
        (bucket_dir / f"{safe_name}.html").write_text(html, encoding="utf-8")

    def _write_comments(self, comments: list[CommentRecord]) -> None:
        for comment in comments:
            key = (
                comment.parent_type,
                comment.parent_id,
                comment.comment_id or comment.comment_url or comment.body or "",
            )
            if key in self._comment_keys:
                continue
            self._comment_keys.add(key)
            self._append_jsonl(self.output_dir / "comments.jsonl", to_dict(comment))
            self.counts["comments"] += 1

    def _write_photos(self, photos: list[PhotoRecord]) -> None:
        for photo in photos:
            key = (photo.parent_type, photo.parent_id, photo.photo_id)
            if key in self._photo_keys:
                continue
            self._photo_keys.add(key)
            self._append_jsonl(self.output_dir / "photos.jsonl", to_dict(photo))
            self.counts["photos"] += 1

    def _write_route_star_records(self, records: list[RouteStarRecord]) -> None:
        for record in records:
            if record.stat_id in self._route_star_keys:
                continue
            self._route_star_keys.add(record.stat_id)
            self._append_jsonl(self.output_dir / "route_stars.jsonl", to_dict(record))
            self.counts["route_stars"] += 1

    def _write_route_rating_records(self, records: list[RouteSuggestedRatingRecord]) -> None:
        for record in records:
            if record.rating_id in self._route_rating_keys:
                continue
            self._route_rating_keys.add(record.rating_id)
            self._append_jsonl(self.output_dir / "route_suggested_ratings.jsonl", to_dict(record))
            self.counts["route_suggested_ratings"] += 1

    def _write_route_todo_records(self, records: list[RouteTodoRecord]) -> None:
        for record in records:
            if record.todo_id in self._route_todo_keys:
                continue
            self._route_todo_keys.add(record.todo_id)
            self._append_jsonl(self.output_dir / "route_todos.jsonl", to_dict(record))
            self.counts["route_todos"] += 1

    def _write_route_tick_records(self, records: list[RouteTickRecord]) -> None:
        for record in records:
            if record.tick_id in self._route_tick_keys:
                continue
            self._route_tick_keys.add(record.tick_id)
            self._append_jsonl(self.output_dir / "route_ticks.jsonl", to_dict(record))
            self.counts["route_ticks"] += 1

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with self._write_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")

    def _load_existing_state(self) -> None:
        for file_name in self._jsonl_files:
            path = self.output_dir / file_name
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    self._record_existing_jsonl_entry(file_name, payload)

    def _record_existing_jsonl_entry(self, file_name: str, payload: dict[str, Any]) -> None:
        if file_name == "areas.jsonl":
            area_id = str(payload.get("area_id") or "")
            area_url = str(payload.get("url") or "")
            if area_id and area_id not in self._area_ids:
                self._area_ids.add(area_id)
                self.counts["areas"] += 1
            if area_url and area_id:
                self._area_url_to_id[area_url] = area_id
            return

        if file_name == "routes.jsonl":
            route_id = str(payload.get("route_id") or "")
            route_url = str(payload.get("url") or "")
            if route_id and route_id not in self._route_ids:
                self._route_ids.add(route_id)
                self.counts["routes"] += 1
            if route_url and route_id:
                self._route_url_to_id[route_url] = route_id
            return

        if file_name == "comments.jsonl":
            key = (
                str(payload.get("parent_type") or ""),
                str(payload.get("parent_id") or ""),
                str(payload.get("comment_id") or payload.get("comment_url") or payload.get("body") or ""),
            )
            if key not in self._comment_keys:
                self._comment_keys.add(key)
                self.counts["comments"] += 1
            return

        if file_name == "photos.jsonl":
            key = (
                str(payload.get("parent_type") or ""),
                str(payload.get("parent_id") or ""),
                str(payload.get("photo_id") or ""),
            )
            if key not in self._photo_keys:
                self._photo_keys.add(key)
                self.counts["photos"] += 1
            return

        if file_name == "route_stats_summary.jsonl":
            route_id = str(payload.get("route_id") or "")
            if route_id and route_id not in self._route_stats_route_ids:
                self._route_stats_route_ids.add(route_id)
                self.counts["route_stats_routes"] += 1
            return

        if file_name == "route_stars.jsonl":
            stat_id = str(payload.get("stat_id") or "")
            if stat_id and stat_id not in self._route_star_keys:
                self._route_star_keys.add(stat_id)
                self.counts["route_stars"] += 1
            return

        if file_name == "route_suggested_ratings.jsonl":
            rating_id = str(payload.get("rating_id") or "")
            if rating_id and rating_id not in self._route_rating_keys:
                self._route_rating_keys.add(rating_id)
                self.counts["route_suggested_ratings"] += 1
            return

        if file_name == "route_todos.jsonl":
            todo_id = str(payload.get("todo_id") or "")
            if todo_id and todo_id not in self._route_todo_keys:
                self._route_todo_keys.add(todo_id)
                self.counts["route_todos"] += 1
            return

        if file_name == "route_ticks.jsonl":
            tick_id = str(payload.get("tick_id") or "")
            if tick_id and tick_id not in self._route_tick_keys:
                self._route_tick_keys.add(tick_id)
                self.counts["route_ticks"] += 1
