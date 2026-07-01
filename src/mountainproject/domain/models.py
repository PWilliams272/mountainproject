from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CommentRecord:
	comment_id: str | None
	parent_type: str
	parent_id: str
	parent_url: str
	author_name: str | None = None
	author_url: str | None = None
	author_meta: str | None = None
	body: str | None = None
	posted_at: str | None = None
	comment_url: str | None = None
	beta_count: int | None = None


@dataclass(slots=True)
class PhotoRecord:
	photo_id: str
	parent_type: str
	parent_id: str
	parent_url: str
	photo_page_url: str
	title: str | None = None
	thumbnail_url: str | None = None
	image_url: str | None = None
	local_path: str | None = None


@dataclass(slots=True)
class AreaRecord:
	area_id: str
	url: str
	name: str
	breadcrumbs: list[str] = field(default_factory=list)
	breadcrumb_urls: list[str] = field(default_factory=list)
	description: str | None = None
	getting_there: str | None = None
	latitude: float | None = None
	longitude: float | None = None
	elevation_ft: int | None = None
	elevation_m: int | None = None
	page_views_total: int | None = None
	page_views_monthly: int | None = None
	shared_by: str | None = None
	shared_date: str | None = None
	details_raw: dict[str, str] = field(default_factory=dict)
	child_area_urls: list[str] = field(default_factory=list)
	route_urls: list[str] = field(default_factory=list)
	photos: list[PhotoRecord] = field(default_factory=list)
	comments: list[CommentRecord] = field(default_factory=list)
	comment_count: int | None = None
	comments_truncated: bool = False
	remaining_comment_count: int | None = None


@dataclass(slots=True)
class RouteRecord:
	route_id: str
	url: str
	name: str
	breadcrumbs: list[str] = field(default_factory=list)
	breadcrumb_urls: list[str] = field(default_factory=list)
	grade_raw: str | None = None
	yds_grade: str | None = None
	route_type_raw: str | None = None
	type_tags: list[str] = field(default_factory=list)
	length_ft: int | None = None
	length_m: int | None = None
	pitches: int | None = None
	latitude: float | None = None
	longitude: float | None = None
	stars_avg: float | None = None
	vote_count: int | None = None
	fa: str | None = None
	page_views_total: int | None = None
	page_views_monthly: int | None = None
	shared_by: str | None = None
	shared_date: str | None = None
	description: str | None = None
	protection: str | None = None
	location: str | None = None
	details_raw: dict[str, str] = field(default_factory=dict)
	photos: list[PhotoRecord] = field(default_factory=list)
	comments: list[CommentRecord] = field(default_factory=list)
	comment_count: int | None = None
	comments_truncated: bool = False
	remaining_comment_count: int | None = None


@dataclass(slots=True)
class RouteStatsSummary:
	route_id: str
	route_url: str
	route_stats_url: str
	stars_count: int = 0
	suggested_ratings_count: int = 0
	todos_count: int = 0
	ticks_count: int = 0


@dataclass(slots=True)
class RouteStarRecord:
	route_id: str
	route_url: str
	stat_id: str
	user_id: str | None = None
	user_name: str | None = None
	score: int | None = None
	created_at: str | None = None
	updated_at: str | None = None


@dataclass(slots=True)
class RouteSuggestedRatingRecord:
	route_id: str
	route_url: str
	rating_id: str
	user_id: str | None = None
	user_name: str | None = None
	all_ratings: list[str] = field(default_factory=list)
	suggested_grade: str | None = None
	rock_rating: int | None = None
	ice_rating: int | None = None
	aid_rating: int | None = None
	boulder_rating: int | None = None
	mixed_rating: int | None = None
	snow_rating: int | None = None
	safety_rating: str | None = None
	created_at: str | None = None
	updated_at: str | None = None


@dataclass(slots=True)
class RouteTodoRecord:
	route_id: str
	route_url: str
	todo_id: str
	user_id: str | None = None
	user_name: str | None = None
	is_in_partner_finder: bool | None = None
	created_at: str | None = None
	updated_at: str | None = None


@dataclass(slots=True)
class RouteTickRecord:
	route_id: str
	route_url: str
	tick_id: str
	user_id: str | None = None
	user_name: str | None = None
	date: str | None = None
	comment: str | None = None
	style: str | None = None
	lead_style: str | None = None
	pitches: int | None = None
	text: str | None = None
	created_at: str | None = None
	updated_at: str | None = None


@dataclass(slots=True)
class RouteStatsBundle:
	summary: RouteStatsSummary
	stars: list[RouteStarRecord] = field(default_factory=list)
	suggested_ratings: list[RouteSuggestedRatingRecord] = field(default_factory=list)
	todos: list[RouteTodoRecord] = field(default_factory=list)
	ticks: list[RouteTickRecord] = field(default_factory=list)


def to_dict(
	value: AreaRecord
	| RouteRecord
	| CommentRecord
	| PhotoRecord
	| RouteStatsSummary
	| RouteStarRecord
	| RouteSuggestedRatingRecord
	| RouteTodoRecord
	| RouteTickRecord
	| RouteStatsBundle
	| dict[str, Any]
) -> dict[str, Any]:
	if isinstance(value, dict):
		return value
	return asdict(value)


def comment_from_dict(payload: dict[str, Any]) -> CommentRecord:
	return CommentRecord(
		comment_id=payload.get("comment_id"),
		parent_type=str(payload.get("parent_type") or ""),
		parent_id=str(payload.get("parent_id") or ""),
		parent_url=str(payload.get("parent_url") or ""),
		author_name=payload.get("author_name"),
		author_url=payload.get("author_url"),
		author_meta=payload.get("author_meta"),
		body=payload.get("body"),
		posted_at=payload.get("posted_at"),
		comment_url=payload.get("comment_url"),
		beta_count=payload.get("beta_count"),
	)


def photo_from_dict(payload: dict[str, Any]) -> PhotoRecord:
	return PhotoRecord(
		photo_id=str(payload.get("photo_id") or ""),
		parent_type=str(payload.get("parent_type") or ""),
		parent_id=str(payload.get("parent_id") or ""),
		parent_url=str(payload.get("parent_url") or ""),
		photo_page_url=str(payload.get("photo_page_url") or ""),
		title=payload.get("title"),
		thumbnail_url=payload.get("thumbnail_url"),
		image_url=payload.get("image_url"),
		local_path=payload.get("local_path"),
	)


def area_from_dict(payload: dict[str, Any]) -> AreaRecord:
	return AreaRecord(
		area_id=str(payload.get("area_id") or ""),
		url=str(payload.get("url") or ""),
		name=str(payload.get("name") or ""),
		breadcrumbs=list(payload.get("breadcrumbs") or []),
		breadcrumb_urls=list(payload.get("breadcrumb_urls") or []),
		description=payload.get("description"),
		getting_there=payload.get("getting_there"),
		latitude=payload.get("latitude"),
		longitude=payload.get("longitude"),
		elevation_ft=payload.get("elevation_ft"),
		elevation_m=payload.get("elevation_m"),
		page_views_total=payload.get("page_views_total"),
		page_views_monthly=payload.get("page_views_monthly"),
		shared_by=payload.get("shared_by"),
		shared_date=payload.get("shared_date"),
		details_raw=dict(payload.get("details_raw") or {}),
		child_area_urls=list(payload.get("child_area_urls") or []),
		route_urls=list(payload.get("route_urls") or []),
		photos=[photo_from_dict(dict(item)) for item in payload.get("photos") or []],
		comments=[comment_from_dict(dict(item)) for item in payload.get("comments") or []],
		comment_count=payload.get("comment_count"),
		comments_truncated=bool(payload.get("comments_truncated") or False),
		remaining_comment_count=payload.get("remaining_comment_count"),
	)


def route_from_dict(payload: dict[str, Any]) -> RouteRecord:
	return RouteRecord(
		route_id=str(payload.get("route_id") or ""),
		url=str(payload.get("url") or ""),
		name=str(payload.get("name") or ""),
		breadcrumbs=list(payload.get("breadcrumbs") or []),
		breadcrumb_urls=list(payload.get("breadcrumb_urls") or []),
		grade_raw=payload.get("grade_raw"),
		yds_grade=payload.get("yds_grade"),
		route_type_raw=payload.get("route_type_raw"),
		type_tags=list(payload.get("type_tags") or []),
		length_ft=payload.get("length_ft"),
		length_m=payload.get("length_m"),
		pitches=payload.get("pitches"),
		latitude=payload.get("latitude"),
		longitude=payload.get("longitude"),
		stars_avg=payload.get("stars_avg"),
		vote_count=payload.get("vote_count"),
		fa=payload.get("fa"),
		page_views_total=payload.get("page_views_total"),
		page_views_monthly=payload.get("page_views_monthly"),
		shared_by=payload.get("shared_by"),
		shared_date=payload.get("shared_date"),
		description=payload.get("description"),
		protection=payload.get("protection"),
		location=payload.get("location"),
		details_raw=dict(payload.get("details_raw") or {}),
		photos=[photo_from_dict(dict(item)) for item in payload.get("photos") or []],
		comments=[comment_from_dict(dict(item)) for item in payload.get("comments") or []],
		comment_count=payload.get("comment_count"),
		comments_truncated=bool(payload.get("comments_truncated") or False),
		remaining_comment_count=payload.get("remaining_comment_count"),
	)
