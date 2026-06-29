from __future__ import annotations

import html
import re
from typing import Any

from ..models import (
    RouteRecord,
    RouteStarRecord,
    RouteStatsBundle,
    RouteStatsSummary,
    RouteSuggestedRatingRecord,
    RouteTickRecord,
    RouteTodoRecord,
)


def build_route_stats_url(route: RouteRecord) -> str:
    return route.url.replace("/route/", "/route/stats/", 1)


def parse_route_stats_bundle(
    route: RouteRecord,
    *,
    stars_items: list[dict[str, Any]],
    rating_items: list[dict[str, Any]],
    todo_items: list[dict[str, Any]],
    tick_items: list[dict[str, Any]],
) -> RouteStatsBundle:
    stars = [parse_route_star_item(route, item) for item in stars_items]
    suggested_ratings = [parse_route_suggested_rating_item(route, item) for item in rating_items]
    todos = [parse_route_todo_item(route, item) for item in todo_items]
    ticks = [parse_route_tick_item(route, item) for item in tick_items]

    summary = RouteStatsSummary(
        route_id=route.route_id,
        route_url=route.url,
        route_stats_url=build_route_stats_url(route),
        stars_count=len(stars),
        suggested_ratings_count=len(suggested_ratings),
        todos_count=len(todos),
        ticks_count=len(ticks),
    )
    return RouteStatsBundle(
        summary=summary,
        stars=stars,
        suggested_ratings=suggested_ratings,
        todos=todos,
        ticks=ticks,
    )


def parse_route_star_item(route: RouteRecord, item: dict[str, Any]) -> RouteStarRecord:
    user = item.get("user") or {}
    return RouteStarRecord(
        route_id=route.route_id,
        route_url=route.url,
        stat_id=str(item.get("id")),
        user_id=_as_str(user.get("id")),
        user_name=_clean_text(user.get("name")),
        score=_as_int(item.get("score")),
        created_at=_as_str(item.get("createdAt")),
        updated_at=_as_str(item.get("updatedAt")),
    )


def parse_route_suggested_rating_item(route: RouteRecord, item: dict[str, Any]) -> RouteSuggestedRatingRecord:
    user = item.get("user") or {}
    all_ratings = [_clean_text(value) for value in item.get("allRatings") or [] if _clean_text(value)]
    return RouteSuggestedRatingRecord(
        route_id=route.route_id,
        route_url=route.url,
        rating_id=str(item.get("id")),
        user_id=_as_str(user.get("id")),
        user_name=_clean_text(user.get("name")),
        all_ratings=all_ratings,
        suggested_grade=", ".join(all_ratings) if all_ratings else None,
        rock_rating=_as_int(item.get("rockRating")),
        ice_rating=_as_int(item.get("iceRating")),
        aid_rating=_as_int(item.get("aidRating")),
        boulder_rating=_as_int(item.get("boulderRating")),
        mixed_rating=_as_int(item.get("mixedRating")),
        snow_rating=_as_int(item.get("snowRating")),
        safety_rating=_clean_text(item.get("safteyRating")),
        created_at=_as_str(item.get("createdAt")),
        updated_at=_as_str(item.get("updatedAt")),
    )


def parse_route_todo_item(route: RouteRecord, item: dict[str, Any]) -> RouteTodoRecord:
    user = item.get("user") or {}
    return RouteTodoRecord(
        route_id=route.route_id,
        route_url=route.url,
        todo_id=str(item.get("id")),
        user_id=_as_str(user.get("id")),
        user_name=_clean_text(user.get("name")),
        is_in_partner_finder=user.get("isInPartnerFinder"),
        created_at=_as_str(item.get("createdAt")),
        updated_at=_as_str(item.get("updatedAt")),
    )


def parse_route_tick_item(route: RouteRecord, item: dict[str, Any]) -> RouteTickRecord:
    user = item.get("user") or {}
    return RouteTickRecord(
        route_id=route.route_id,
        route_url=route.url,
        tick_id=str(item.get("id")),
        user_id=_as_str(user.get("id")),
        user_name=_clean_text(user.get("name")),
        date=_clean_text(item.get("date")),
        comment=_clean_text(item.get("comment")),
        style=_clean_text(item.get("style")),
        lead_style=_clean_text(item.get("leadStyle")),
        pitches=_as_int(item.get("pitches")),
        text=_clean_api_text(item.get("text")),
        created_at=_as_str(item.get("createdAt") or item.get("createAt")),
        updated_at=_as_str(item.get("updatedAt")),
    )


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _clean_api_text(value: Any) -> str | None:
    text = _clean_text(html.unescape(str(value))) if value is not None else None
    if text is None:
        return None
    return text.replace("· ", "").strip() or None