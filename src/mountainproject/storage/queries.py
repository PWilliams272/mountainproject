from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .loaders import LoadedExports, unique_row_count


@dataclass(slots=True)
class CommentAuditResult:
    output_name: str | None
    truncated_routes: pd.DataFrame
    truncated_areas: pd.DataFrame

    @property
    def missing_route_comments(self) -> int:
        if self.truncated_routes.empty:
            return 0
        return int(self.truncated_routes["remaining_comment_count"].fillna(0).sum())

    @property
    def missing_area_comments(self) -> int:
        if self.truncated_areas.empty:
            return 0
        return int(self.truncated_areas["remaining_comment_count"].fillna(0).sum())


def dataset_counts(loaded: LoadedExports) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"dataset": "areas", "rows_loaded": len(loaded.areas), "unique_records": unique_row_count(loaded.areas, ["area_id"])},
            {"dataset": "routes", "rows_loaded": len(loaded.routes), "unique_records": unique_row_count(loaded.routes, ["route_id"])},
            {"dataset": "comments", "rows_loaded": len(loaded.comments), "unique_records": unique_row_count(loaded.comments, ["comment_id", "comment_url", "parent_type", "parent_id", "body"])},
            {"dataset": "photos", "rows_loaded": len(loaded.photos), "unique_records": unique_row_count(loaded.photos, ["parent_type", "parent_id", "photo_id"])},
            {"dataset": "route_stats_summary", "rows_loaded": len(loaded.route_stats_summary), "unique_records": unique_row_count(loaded.route_stats_summary, ["route_id"])},
            {"dataset": "route_stars", "rows_loaded": len(loaded.route_stars), "unique_records": unique_row_count(loaded.route_stars, ["stat_id"])},
            {"dataset": "route_suggested_ratings", "rows_loaded": len(loaded.route_suggested_ratings), "unique_records": unique_row_count(loaded.route_suggested_ratings, ["rating_id"])},
            {"dataset": "route_todos", "rows_loaded": len(loaded.route_todos), "unique_records": unique_row_count(loaded.route_todos, ["todo_id"])},
            {"dataset": "route_ticks", "rows_loaded": len(loaded.route_ticks), "unique_records": unique_row_count(loaded.route_ticks, ["tick_id"])},
        ]
    )


def manifest_summary(loaded: LoadedExports) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "output_name": output_name,
                "start_url": payload.get("start_url"),
                "finished_at": payload.get("finished_at"),
                "auth_mode": payload.get("auth_mode"),
                "full_depth": payload.get("full_depth"),
                "http_cache_mode": payload.get("http_cache_mode"),
                "reuse_existing_data": payload.get("reuse_existing_data"),
                "route_workers": payload.get("route_workers"),
                "route_stats_workers": payload.get("route_stats_workers"),
                "areas": (payload.get("counts") or {}).get("areas"),
                "routes": (payload.get("counts") or {}).get("routes"),
                "comments": (payload.get("counts") or {}).get("comments"),
                "photos": (payload.get("counts") or {}).get("photos"),
                "route_stats_routes": (payload.get("counts") or {}).get("route_stats_routes"),
                "route_ticks": (payload.get("counts") or {}).get("route_ticks"),
                "has_manifest": bool(payload),
            }
            for output_name, payload in loaded.manifests.items()
        ]
    ).sort_values("output_name").reset_index(drop=True)


def comment_audit(loaded: LoadedExports, output_name: str | None = None) -> CommentAuditResult:
    if output_name is None:
        output_name = loaded.selected_outputs[0] if loaded.selected_outputs else None

    truncated_routes = pd.DataFrame()
    if not loaded.routes.empty and output_name is not None:
        truncated_routes = (
            loaded.routes[
                (loaded.routes["output_name"] == output_name)
                & loaded.routes["comments_truncated"].fillna(False)
            ][["route_id", "name", "comment_count", "remaining_comment_count"]]
            .sort_values(["remaining_comment_count", "comment_count"], ascending=[False, False])
            .reset_index(drop=True)
        )

    truncated_areas = pd.DataFrame()
    if not loaded.areas.empty and output_name is not None:
        truncated_areas = (
            loaded.areas[
                (loaded.areas["output_name"] == output_name)
                & loaded.areas["comments_truncated"].fillna(False)
            ][["area_id", "name", "comment_count", "remaining_comment_count"]]
            .sort_values(["remaining_comment_count", "comment_count"], ascending=[False, False])
            .reset_index(drop=True)
        )

    return CommentAuditResult(
        output_name=output_name,
        truncated_routes=truncated_routes,
        truncated_areas=truncated_areas,
    )


def route_comment_counts(loaded: LoadedExports, output_name: str | None = None) -> pd.DataFrame:
    routes = loaded.routes
    comments = loaded.comments
    if output_name is not None and not routes.empty:
        routes = routes[routes["output_name"] == output_name].copy()
    if output_name is not None and not comments.empty:
        comments = comments[comments["output_name"] == output_name].copy()

    route_comment_counts_frame = (
        comments.loc[comments["parent_type"].eq("route"), ["parent_id"]]
        .rename(columns={"parent_id": "route_id"})
        .assign(route_id=lambda df: pd.to_numeric(df["route_id"], errors="coerce"))
        .dropna(subset=["route_id"])
        .assign(route_id=lambda df: df["route_id"].astype("int64"))
        .groupby("route_id", as_index=False)
        .size()
        .rename(columns={"size": "comment_rows"})
    ) if not comments.empty else pd.DataFrame(columns=["route_id", "comment_rows"])

    if routes.empty:
        return pd.DataFrame()

    return (
        routes[[
            "output_name",
            "route_id",
            "name",
            "yds_grade",
            "route_type_raw",
            "page_views_total",
            "comment_count",
            "comments_truncated",
        ]]
        .assign(route_id=lambda df: pd.to_numeric(df["route_id"], errors="coerce"))
        .dropna(subset=["route_id"])
        .assign(route_id=lambda df: df["route_id"].astype("int64"))
        .merge(route_comment_counts_frame, on="route_id", how="left")
        .assign(comment_rows=lambda df: df["comment_rows"].fillna(0).astype("int64"))
        .assign(comment_count=lambda df: pd.to_numeric(df["comment_count"], errors="coerce"))
        .assign(comment_count=lambda df: df["comment_count"].fillna(0).astype("int64"))
        .assign(comment_delta=lambda df: df["comment_rows"] - df["comment_count"])
        .sort_values(["comment_rows", "comment_count", "page_views_total", "name"], ascending=[False, False, False, True])
        .reset_index(drop=True)
    )


def top_routes_by_page_views(
    loaded: LoadedExports,
    output_name: str | None = None,
    limit: int = 25,
) -> pd.DataFrame:
    routes = loaded.routes
    if output_name is not None and not routes.empty:
        routes = routes[routes["output_name"] == output_name].copy()
    if routes.empty:
        return pd.DataFrame()
    columns = [
        "output_name",
        "route_id",
        "name",
        "yds_grade",
        "route_type_raw",
        "page_views_total",
        "stars_avg",
        "vote_count",
        "comment_count",
        "comments_truncated",
    ]
    available_columns = [column for column in columns if column in routes.columns]
    return routes[available_columns].sort_values(["page_views_total", "vote_count"], ascending=[False, False]).head(limit)


def top_routes_by_ticks(
    loaded: LoadedExports,
    output_name: str | None = None,
    limit: int = 25,
) -> pd.DataFrame:
    routes = loaded.routes
    route_stats_summary = loaded.route_stats_summary
    if output_name is not None and not routes.empty:
        routes = routes[routes["output_name"] == output_name].copy()
    if output_name is not None and not route_stats_summary.empty:
        route_stats_summary = route_stats_summary[route_stats_summary["output_name"] == output_name].copy()
    if routes.empty:
        return pd.DataFrame()
    if route_stats_summary.empty:
        stats_subset = pd.DataFrame(columns=["output_name", "route_id", "stars_count", "suggested_ratings_count", "todos_count", "ticks_count"])
    else:
        stats_subset = route_stats_summary[[
            "output_name",
            "route_id",
            "stars_count",
            "suggested_ratings_count",
            "todos_count",
            "ticks_count",
        ]]
    return (
        routes.merge(stats_subset, on=["output_name", "route_id"], how="left")[[
            "output_name",
            "route_id",
            "name",
            "yds_grade",
            "route_type_raw",
            "page_views_total",
            "stars_count",
            "suggested_ratings_count",
            "todos_count",
            "ticks_count",
        ]]
        .sort_values(["ticks_count", "page_views_total"], ascending=[False, False])
        .head(limit)
    )