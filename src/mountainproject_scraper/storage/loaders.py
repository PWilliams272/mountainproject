from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


TABLE_FILES = {
    "areas": "areas.jsonl",
    "routes": "routes.jsonl",
    "comments": "comments.jsonl",
    "photos": "photos.jsonl",
    "route_stats_summary": "route_stats_summary.jsonl",
    "route_stars": "route_stars.jsonl",
    "route_suggested_ratings": "route_suggested_ratings.jsonl",
    "route_todos": "route_todos.jsonl",
    "route_ticks": "route_ticks.jsonl",
}


@dataclass(slots=True)
class LoadedExports:
    project_root: Path
    data_root: Path
    export_root: Path
    output_dirs: dict[str, Path]
    manifests: dict[str, dict[str, Any]]
    areas: pd.DataFrame
    routes: pd.DataFrame
    comments: pd.DataFrame
    photos: pd.DataFrame
    route_stats_summary: pd.DataFrame
    route_stars: pd.DataFrame
    route_suggested_ratings: pd.DataFrame
    route_todos: pd.DataFrame
    route_ticks: pd.DataFrame
    duckdb_connection: duckdb.DuckDBPyConnection | None = None
    duckdb_path: Path | None = None

    @property
    def selected_outputs(self) -> list[str]:
        return list(self.output_dirs)

    @property
    def output_dir(self) -> Path | None:
        return next(iter(self.output_dirs.values())) if len(self.output_dirs) == 1 else None

    def table(self, name: str) -> pd.DataFrame:
        return getattr(self, name)

    def close(self) -> None:
        if self.duckdb_connection is not None:
            self.duckdb_connection.close()
            self.duckdb_connection = None


def resolve_project_root(cwd: Path | None = None) -> Path:
    cwd = (cwd or Path.cwd()).resolve()
    return cwd if (cwd / "src").exists() else cwd.parent


def resolve_data_root(project_root: Path | None = None) -> Path:
    return (project_root or resolve_project_root()) / "data"


def resolve_export_root(project_root: Path | None = None, export_root: Path | None = None) -> Path:
    if export_root is not None:
        return export_root.resolve()
    return resolve_data_root(project_root) / "exports"


def discover_export_dirs(
    export_root: Path | None = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Path]:
    root = resolve_export_root(project_root, export_root)
    available_outputs: dict[str, Path] = {}
    if not root.exists():
        return available_outputs

    for manifest_path in root.glob("*/manifest.json"):
        output_dir = manifest_path.parent
        available_outputs[output_dir.name] = output_dir
    for routes_path in root.glob("*/routes.jsonl"):
        output_dir = routes_path.parent
        available_outputs.setdefault(output_dir.name, output_dir)
    return dict(sorted(available_outputs.items()))


def read_jsonl(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_json(path, lines=True)


def read_jsonl_many(output_dirs: dict[str, Path], file_name: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for output_name, output_dir in output_dirs.items():
        frame = read_jsonl(output_dir / file_name)
        if frame.empty:
            continue
        frame = frame.copy()
        frame.insert(0, "output_name", output_name)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def unique_row_count(frame: pd.DataFrame, columns: list[str]) -> int:
    if frame.empty:
        return 0
    available_columns = [column for column in columns if column in frame.columns]
    if not available_columns:
        return int(len(frame))
    return int(frame[available_columns].drop_duplicates().shape[0])


def load_manifests(output_dirs: dict[str, Path]) -> dict[str, dict[str, Any]]:
    manifests: dict[str, dict[str, Any]] = {}
    for name, output_dir in output_dirs.items():
        manifest_path = output_dir / "manifest.json"
        manifests[name] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    return manifests


def select_output_dirs(
    *,
    output_names: list[str] | None = None,
    prefer_names: list[str] | None = None,
    select_all: bool = False,
    export_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Path]:
    available_outputs = discover_export_dirs(export_root, project_root=project_root)
    if output_names:
        selected_names = [name for name in output_names if name in available_outputs]
    elif select_all:
        selected_names = list(available_outputs)
    else:
        prefer_names = prefer_names or []
        preferred_name = next((name for name in prefer_names if name in available_outputs), None)
        fallback_name = next(iter(available_outputs), None)
        selected_names = [preferred_name or fallback_name] if (preferred_name or fallback_name) else []
    return {name: available_outputs[name] for name in selected_names if name in available_outputs}


def load_exports(
    *,
    output_names: list[str] | None = None,
    prefer_names: list[str] | None = None,
    select_all: bool = False,
    export_root: Path | None = None,
    project_root: Path | None = None,
    open_duckdb: bool = True,
) -> LoadedExports:
    project_root = resolve_project_root(project_root)
    data_root = resolve_data_root(project_root)
    export_root = resolve_export_root(project_root, export_root)
    output_dirs = select_output_dirs(
        output_names=output_names,
        prefer_names=prefer_names,
        select_all=select_all,
        export_root=export_root,
        project_root=project_root,
    )
    manifests = load_manifests(output_dirs)

    areas = read_jsonl_many(output_dirs, TABLE_FILES["areas"])
    routes = read_jsonl_many(output_dirs, TABLE_FILES["routes"])
    comments = read_jsonl_many(output_dirs, TABLE_FILES["comments"])
    photos = read_jsonl_many(output_dirs, TABLE_FILES["photos"])
    route_stats_summary = read_jsonl_many(output_dirs, TABLE_FILES["route_stats_summary"])
    route_stars = read_jsonl_many(output_dirs, TABLE_FILES["route_stars"])
    route_suggested_ratings = read_jsonl_many(output_dirs, TABLE_FILES["route_suggested_ratings"])
    route_todos = read_jsonl_many(output_dirs, TABLE_FILES["route_todos"])
    route_ticks = read_jsonl_many(output_dirs, TABLE_FILES["route_ticks"])

    single_output_dir = next(iter(output_dirs.values())) if len(output_dirs) == 1 else None
    duckdb_path = single_output_dir / "structured" / "mountainproject.duckdb" if single_output_dir else None
    duckdb_connection = (
        duckdb.connect(str(duckdb_path), read_only=True)
        if open_duckdb and duckdb_path is not None and duckdb_path.exists()
        else None
    )

    return LoadedExports(
        project_root=project_root,
        data_root=data_root,
        export_root=export_root,
        output_dirs=output_dirs,
        manifests=manifests,
        areas=areas,
        routes=routes,
        comments=comments,
        photos=photos,
        route_stats_summary=route_stats_summary,
        route_stars=route_stars,
        route_suggested_ratings=route_suggested_ratings,
        route_todos=route_todos,
        route_ticks=route_ticks,
        duckdb_connection=duckdb_connection,
        duckdb_path=duckdb_path,
    )
