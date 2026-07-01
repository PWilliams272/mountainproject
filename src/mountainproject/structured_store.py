from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StructuredTableSpec:
    table_name: str
    jsonl_name: str
    select_sql: str

    def build_query(self, jsonl_path: Path) -> str:
        escaped_path = jsonl_path.as_posix().replace("'", "''")
        return (
            f"SELECT\n{self.select_sql}\n"
            f"FROM read_json_auto('{escaped_path}', format='newline_delimited')"
        )


TABLE_SPECS = (
    StructuredTableSpec(
        table_name="areas",
        jsonl_name="areas.jsonl",
        select_sql="""
    TRY_CAST(area_id AS BIGINT) AS area_id,
    url,
    name,
    to_json(breadcrumbs) AS breadcrumbs_json,
    to_json(breadcrumb_urls) AS breadcrumb_urls_json,
    description,
    getting_there,
    TRY_CAST(latitude AS DOUBLE) AS latitude,
    TRY_CAST(longitude AS DOUBLE) AS longitude,
    TRY_CAST(elevation_ft AS BIGINT) AS elevation_ft,
    TRY_CAST(elevation_m AS BIGINT) AS elevation_m,
    TRY_CAST(page_views_total AS BIGINT) AS page_views_total,
    TRY_CAST(page_views_monthly AS BIGINT) AS page_views_monthly,
    shared_by,
    shared_date,
    to_json(details_raw) AS details_raw_json,
    to_json(child_area_urls) AS child_area_urls_json,
    to_json(route_urls) AS route_urls_json,
    TRY_CAST(route_count AS BIGINT) AS route_count,
    TRY_CAST(photo_count AS BIGINT) AS photo_count,
    TRY_CAST(comment_count AS BIGINT) AS comment_count,
    TRY_CAST(comments_truncated AS BOOLEAN) AS comments_truncated,
    TRY_CAST(remaining_comment_count AS BIGINT) AS remaining_comment_count
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="routes",
        jsonl_name="routes.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    url,
    name,
    to_json(breadcrumbs) AS breadcrumbs_json,
    to_json(breadcrumb_urls) AS breadcrumb_urls_json,
    grade_raw,
    yds_grade,
    route_type_raw,
    to_json(type_tags) AS type_tags_json,
    TRY_CAST(length_ft AS BIGINT) AS length_ft,
    TRY_CAST(length_m AS BIGINT) AS length_m,
    TRY_CAST(pitches AS BIGINT) AS pitches,
    TRY_CAST(latitude AS DOUBLE) AS latitude,
    TRY_CAST(longitude AS DOUBLE) AS longitude,
    TRY_CAST(stars_avg AS DOUBLE) AS stars_avg,
    TRY_CAST(vote_count AS BIGINT) AS vote_count,
    fa,
    TRY_CAST(page_views_total AS BIGINT) AS page_views_total,
    TRY_CAST(page_views_monthly AS BIGINT) AS page_views_monthly,
    shared_by,
    shared_date,
    description,
    protection,
    location,
    to_json(details_raw) AS details_raw_json,
    TRY_CAST(photo_count AS BIGINT) AS photo_count,
    TRY_CAST(comment_count AS BIGINT) AS comment_count,
    TRY_CAST(comments_truncated AS BOOLEAN) AS comments_truncated,
    TRY_CAST(remaining_comment_count AS BIGINT) AS remaining_comment_count
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="comments",
        jsonl_name="comments.jsonl",
        select_sql="""
    comment_id,
    parent_type,
    TRY_CAST(parent_id AS BIGINT) AS parent_id,
    parent_url,
    author_name,
    author_url,
    author_meta,
    body,
    posted_at,
    comment_url,
    TRY_CAST(beta_count AS BIGINT) AS beta_count
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="photos",
        jsonl_name="photos.jsonl",
        select_sql="""
    TRY_CAST(photo_id AS BIGINT) AS photo_id,
    parent_type,
    TRY_CAST(parent_id AS BIGINT) AS parent_id,
    parent_url,
    photo_page_url,
    title,
    thumbnail_url,
    image_url,
    local_path
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="route_stats_summary",
        jsonl_name="route_stats_summary.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    route_url,
    route_stats_url,
    TRY_CAST(stars_count AS BIGINT) AS stars_count,
    TRY_CAST(suggested_ratings_count AS BIGINT) AS suggested_ratings_count,
    TRY_CAST(todos_count AS BIGINT) AS todos_count,
    TRY_CAST(ticks_count AS BIGINT) AS ticks_count
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="route_stars",
        jsonl_name="route_stars.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    route_url,
    TRY_CAST(stat_id AS BIGINT) AS stat_id,
    TRY_CAST(user_id AS BIGINT) AS user_id,
    user_name,
    TRY_CAST(score AS BIGINT) AS score,
    created_at,
    updated_at
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="route_suggested_ratings",
        jsonl_name="route_suggested_ratings.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    route_url,
    TRY_CAST(rating_id AS BIGINT) AS rating_id,
    TRY_CAST(user_id AS BIGINT) AS user_id,
    user_name,
    to_json(all_ratings) AS all_ratings_json,
    suggested_grade,
    TRY_CAST(rock_rating AS BIGINT) AS rock_rating,
    TRY_CAST(ice_rating AS BIGINT) AS ice_rating,
    TRY_CAST(aid_rating AS BIGINT) AS aid_rating,
    TRY_CAST(boulder_rating AS BIGINT) AS boulder_rating,
    TRY_CAST(mixed_rating AS BIGINT) AS mixed_rating,
    TRY_CAST(snow_rating AS BIGINT) AS snow_rating,
    safety_rating,
    created_at,
    updated_at
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="route_todos",
        jsonl_name="route_todos.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    route_url,
    TRY_CAST(todo_id AS BIGINT) AS todo_id,
    TRY_CAST(user_id AS BIGINT) AS user_id,
    user_name,
    TRY_CAST(is_in_partner_finder AS BOOLEAN) AS is_in_partner_finder,
    created_at,
    updated_at
        """.strip(),
    ),
    StructuredTableSpec(
        table_name="route_ticks",
        jsonl_name="route_ticks.jsonl",
        select_sql="""
    TRY_CAST(route_id AS BIGINT) AS route_id,
    route_url,
    TRY_CAST(tick_id AS BIGINT) AS tick_id,
    TRY_CAST(user_id AS BIGINT) AS user_id,
    user_name,
    date,
    comment,
    style,
    lead_style,
    TRY_CAST(pitches AS BIGINT) AS pitches,
    text,
    created_at,
    updated_at
        """.strip(),
    ),
)


class StructuredLocalStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.base_dir = output_dir / "structured"
        self.parquet_dir = self.base_dir / "parquet"
        self.database_path = self.base_dir / "mountainproject.duckdb"

    def sync(self, manifest: dict[str, object]) -> dict[str, str]:
        import duckdb

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.parquet_dir.mkdir(parents=True, exist_ok=True)

        connection = duckdb.connect(str(self.database_path))
        try:
            for spec in TABLE_SPECS:
                jsonl_path = self.output_dir / spec.jsonl_name
                if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
                    continue
                query = spec.build_query(jsonl_path)
                parquet_path = self.parquet_dir / f"{spec.table_name}.parquet"
                if parquet_path.exists():
                    parquet_path.unlink()
                connection.execute(f"CREATE OR REPLACE TABLE {spec.table_name} AS {query}")
                connection.execute(
                    f"COPY {spec.table_name} TO '{self._escape_sql_path(parquet_path)}' (FORMAT PARQUET)"
                )

            manifest_path = self.base_dir / "manifest_snapshot.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            connection.close()

        return {
            "database_path": str(self.database_path),
            "parquet_dir": str(self.parquet_dir),
            "manifest_snapshot": str(self.base_dir / "manifest_snapshot.json"),
        }

    def _escape_sql_path(self, path: Path) -> str:
        return path.as_posix().replace("'", "''")