from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import RouteStatsBundle, area_from_dict, route_from_dict


@dataclass(frozen=True)
class StoredObjectRef:
    export_name: str
    object_id: str
    path: Path


class ExportCatalog:
    def __init__(
        self,
        *,
        root: Path,
        current_output_dir: Path | None = None,
        auth_mode: str | None = None,
    ) -> None:
        self.root = root
        self.current_output_dir = current_output_dir.resolve() if current_output_dir is not None else None
        self.auth_mode = auth_mode
        self._area_refs: dict[str, StoredObjectRef] = {}
        self._route_refs: dict[str, StoredObjectRef] = {}
        self._route_stats_refs: dict[str, StoredObjectRef] = {}
        self._scanned = False

    def load_area_by_url(self, area_url: str):
        self._ensure_scanned()
        ref = self._area_refs.get(area_url)
        if ref is None or not ref.path.exists():
            return None
        return area_from_dict(json.loads(ref.path.read_text(encoding="utf-8")))

    def load_route_by_url(self, route_url: str):
        self._ensure_scanned()
        ref = self._route_refs.get(route_url)
        if ref is None or not ref.path.exists():
            return None
        return route_from_dict(json.loads(ref.path.read_text(encoding="utf-8")))

    def has_route_stats(self, route_id: str) -> bool:
        self._ensure_scanned()
        ref = self._route_stats_refs.get(str(route_id))
        return ref is not None and ref.path.exists()

    def load_route_stats(self, route_id: str) -> RouteStatsBundle | None:
        self._ensure_scanned()
        ref = self._route_stats_refs.get(str(route_id))
        if ref is None or not ref.path.exists():
            return None
        payload = json.loads(ref.path.read_text(encoding="utf-8"))
        return route_stats_bundle_from_dict(payload)

    def _ensure_scanned(self) -> None:
        if self._scanned:
            return
        self._scanned = True
        if not self.root.exists():
            return

        for export_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            if self.current_output_dir is not None and export_dir.resolve() == self.current_output_dir:
                continue
            if not self._should_include_export(export_dir):
                continue
            self._index_export(export_dir)

    def _should_include_export(self, export_dir: Path) -> bool:
        routes_jsonl = export_dir / "routes.jsonl"
        manifest_path = export_dir / "manifest.json"
        if not routes_jsonl.exists() and not manifest_path.exists():
            return False
        if self.auth_mode is None or not manifest_path.exists():
            return True

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        export_auth_mode = manifest.get("auth_mode")
        if export_auth_mode is None:
            return False
        return str(export_auth_mode) == self.auth_mode

    def _index_export(self, export_dir: Path) -> None:
        export_name = export_dir.name

        areas_dir = export_dir / "areas"
        if areas_dir.exists():
            for path in sorted(areas_dir.glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                area_url = str(payload.get("url") or "")
                area_id = str(payload.get("area_id") or path.stem)
                if area_url and area_url not in self._area_refs:
                    self._area_refs[area_url] = StoredObjectRef(export_name, area_id, path)

        routes_dir = export_dir / "routes"
        if routes_dir.exists():
            for path in sorted(routes_dir.glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                route_url = str(payload.get("url") or "")
                route_id = str(payload.get("route_id") or path.stem)
                if route_url and route_url not in self._route_refs:
                    self._route_refs[route_url] = StoredObjectRef(export_name, route_id, path)

        route_stats_dir = export_dir / "route_stats"
        if route_stats_dir.exists():
            for path in sorted(route_stats_dir.glob("*.json")):
                route_id = path.stem
                if route_id not in self._route_stats_refs:
                    self._route_stats_refs[route_id] = StoredObjectRef(export_name, route_id, path)


def route_stats_bundle_from_dict(payload: dict[str, object]) -> RouteStatsBundle:
    from .models import (
        RouteStarRecord,
        RouteStatsSummary,
        RouteSuggestedRatingRecord,
        RouteTickRecord,
        RouteTodoRecord,
    )

    summary_payload = dict(payload.get("summary") or {})
    summary = RouteStatsSummary(
        route_id=str(summary_payload.get("route_id") or ""),
        route_url=str(summary_payload.get("route_url") or ""),
        route_stats_url=str(summary_payload.get("route_stats_url") or ""),
        stars_count=int(summary_payload.get("stars_count") or 0),
        suggested_ratings_count=int(summary_payload.get("suggested_ratings_count") or 0),
        todos_count=int(summary_payload.get("todos_count") or 0),
        ticks_count=int(summary_payload.get("ticks_count") or 0),
    )

    stars = [RouteStarRecord(**dict(item)) for item in payload.get("stars") or []]
    suggested_ratings = [
        RouteSuggestedRatingRecord(**dict(item))
        for item in payload.get("suggested_ratings") or []
    ]
    todos = [RouteTodoRecord(**dict(item)) for item in payload.get("todos") or []]
    ticks = [RouteTickRecord(**dict(item)) for item in payload.get("ticks") or []]
    return RouteStatsBundle(
        summary=summary,
        stars=stars,
        suggested_ratings=suggested_ratings,
        todos=todos,
        ticks=ticks,
    )