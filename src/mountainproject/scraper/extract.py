from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from ..domain.models import AreaRecord, CommentRecord, PhotoRecord, RouteRecord

HEADING_NAMES = {"h1", "h2", "h3"}
MOUNTAINPROJECT_HOSTS = {"mountainproject.com", "www.mountainproject.com"}
US_STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def absolute_url(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def canonical_page_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def is_mountainproject_page_url(url: str) -> bool:
    parts = urlsplit(url)
    if not parts.netloc:
        return True
    return parts.netloc.lower() in MOUNTAINPROJECT_HOSTS


def is_route_page_url(url: str) -> bool:
    if not is_mountainproject_page_url(url):
        return False
    path_segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not path_segments or path_segments[0] != "route":
        return False
    return any(segment.isdigit() for segment in path_segments[1:])


def extract_object_id(url: str, object_type: str) -> str | None:
    path_segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    try:
        object_index = path_segments.index(object_type)
    except ValueError:
        return None

    for segment in path_segments[object_index + 1 :]:
        if segment.isdigit():
            return segment
    return None


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, list):
            objects.extend(item for item in loaded if isinstance(item, dict))
        elif isinstance(loaded, dict):
            objects.append(loaded)
    return objects


def parse_breadcrumbs(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    schema = next(
        (obj for obj in parse_json_ld_objects(soup) if obj.get("@type") == "BreadcrumbList"),
        None,
    )
    if schema:
        names: list[str] = []
        urls: list[str] = []
        for item in schema.get("itemListElement", []):
            name = clean_text(item.get("name"))
            item_url = item.get("item")
            if name:
                names.append(name)
            if item_url:
                urls.append(canonical_page_url(item_url))
        return names, urls

    breadcrumb_container = soup.select_one("div.mb-half.small.text-warm")
    crumb_links = breadcrumb_container.select("a[href]") if breadcrumb_container is not None else []
    names = [clean_text(link.get_text(" ", strip=True)) for link in crumb_links]
    urls = [canonical_page_url(link["href"]) for link in crumb_links if link.get("href")]
    return names, urls


def parse_route_guide_state_area_urls(
    html: str,
    *,
    page_url: str = "https://www.mountainproject.com/route-guide",
) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    state_area_urls: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for link in soup.select('a[href*="/area/"]'):
        state_name = clean_text(link.get_text(" ", strip=True))
        if state_name not in US_STATE_NAMES:
            continue
        href = absolute_url(page_url, link.get("href"))
        if not href:
            continue
        href = canonical_page_url(href)
        if not is_mountainproject_page_url(href):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)
        state_area_urls.append((state_name, href))

    return sorted(state_area_urls, key=lambda item: item[0])


def parse_child_area_urls(
    soup: BeautifulSoup,
    *,
    page_url: str,
    current_area_id: str,
    breadcrumb_urls: list[str],
) -> list[str]:
    area_section_heading = next(
        (
            heading
            for heading in soup.select("h3")
            if clean_text(heading.get_text(" ", strip=True)).startswith("Areas in ")
        ),
        None,
    )

    if area_section_heading is not None:
        area_section_root = area_section_heading.find_parent(class_="mp-sidebar") or area_section_heading.parent
        candidate_links = area_section_root.select('a[href*="/area/"]') if isinstance(area_section_root, Tag) else []
    else:
        candidate_links = soup.select('a[href*="/area/"]')

    current_url = canonical_page_url(page_url)
    child_area_urls: list[str] = []
    for link in candidate_links:
        href = absolute_url(page_url, link.get("href"))
        if not href:
            continue
        href = canonical_page_url(href)
        if not is_mountainproject_page_url(href):
            continue
        href_area_id = extract_object_id(href, "area")
        if href_area_id is None:
            continue
        if href_area_id == current_area_id:
            continue
        if href == current_url:
            continue
        if href in breadcrumb_urls:
            continue
        if "/area/classics/" in href or href.endswith("/route-guide"):
            continue
        if "?print=1" in link.get("href", ""):
            continue
        child_area_urls.append(href)
    return dedupe_keep_order(child_area_urls)


def extract_details_rows(soup: BeautifulSoup) -> dict[str, str]:
    table = soup.select_one("table.description-details")
    if not table:
        return {}

    details: dict[str, str] = {}
    for row in table.select("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        key = cells[0].rstrip(":")
        value = clean_text(" ".join(cells[1:]))
        if key and value:
            details[key] = value
    return details


def extract_details_text(soup: BeautifulSoup) -> str:
    table = soup.select_one("table.description-details")
    if not table:
        return ""
    return clean_text(table.get_text(" ", strip=True))


def extract_section_text(soup: BeautifulSoup, heading_label: str) -> str | None:
    heading = soup.find(
        lambda tag: isinstance(tag, Tag)
        and tag.name in HEADING_NAMES
        and heading_label.lower() in clean_text(tag.get_text(" ", strip=True)).lower()
    )
    if not heading:
        return None

    parts: list[str] = []
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and sibling.name in HEADING_NAMES:
            break
        if isinstance(sibling, Tag):
            text = sibling.get_text("\n", strip=True)
            if text:
                parts.append(text)
        elif isinstance(sibling, NavigableString):
            text = clean_text(str(sibling))
            if text:
                parts.append(text)

    if not parts:
        return None
    return "\n\n".join(part for part in parts if part)


def parse_float_pair(text: str) -> tuple[float | None, float | None]:
    match = re.search(r"GPS:\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", text)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def parse_onx_coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:
    for link in soup.select('a[href*="onxmaps.com"]'):
        href = link.get("href")
        if not href:
            continue
        match = re.search(r"#\d+/(-?\d+\.\d+)/(-?\d+\.\d+)/", href)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None, None


def parse_geo_from_schema(soup: BeautifulSoup) -> tuple[float | None, float | None]:
    for obj in parse_json_ld_objects(soup):
        geo = obj.get("geo")
        if not isinstance(geo, dict):
            continue
        latitude = geo.get("latitude")
        longitude = geo.get("longitude")
        if latitude is None or longitude is None:
            continue
        try:
            return float(latitude), float(longitude)
        except (TypeError, ValueError):
            continue
    return None, None


def parse_rating(soup: BeautifulSoup) -> tuple[float | None, int | None]:
    for obj in parse_json_ld_objects(soup):
        aggregate = obj.get("aggregateRating")
        if not isinstance(aggregate, dict):
            continue
        try:
            stars = float(aggregate.get("ratingValue")) if aggregate.get("ratingValue") else None
        except (TypeError, ValueError):
            stars = None
        try:
            votes = int(str(aggregate.get("reviewCount", "")).replace(",", ""))
        except ValueError:
            votes = None
        if stars is not None or votes is not None:
            return stars, votes

    body_text = clean_text(soup.get_text(" ", strip=True))
    match = re.search(r"Avg:\s*([0-9.]+)\s*from\s*([\d,]+)\s*votes", body_text)
    if not match:
        return None, None
    return float(match.group(1)), int(match.group(2).replace(",", ""))


def parse_page_views(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        r"Page Views:\s*([\d,]+)\s*total(?:\s*[·•]\s*|\s+)([\d,]+)\s*/month",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def parse_shared_by(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"Shared By:\s*(.*?)\s+on\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", text)
    if not match:
        return None, None
    return clean_text(match.group(1)), match.group(2)


def parse_elevation(text: str) -> tuple[int | None, int | None]:
    match = re.search(r"Elevation:\s*([\d,]+)\s*ft\s*([\d,]+)\s*m", text)
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def parse_route_specs(text: str) -> tuple[str | None, list[str], int | None, int | None, int | None]:
    type_match = re.search(
        r"Type:\s*(.*?)\s*(?=(?:[\d,]+\s*ft\b)|(?:\d+\s*pitches?\b)|GPS:|FA:|Page Views:|Shared By:|Admins:|$)",
        text,
        re.IGNORECASE,
    )
    route_type_raw = clean_text(type_match.group(1)).rstrip(",") if type_match else None
    type_tags = [clean_text(part) for part in (route_type_raw or "").split(",") if clean_text(part)]

    length_match = re.search(r"([\d,]+)\s*ft\s*\(([\d,]+)\s*m\)", text)
    length_ft = int(length_match.group(1).replace(",", "")) if length_match else None
    length_m = int(length_match.group(2).replace(",", "")) if length_match else None

    pitch_match = re.search(r"(\d+)\s*pitches?", text, re.IGNORECASE)
    pitches = int(pitch_match.group(1)) if pitch_match else None
    return route_type_raw, type_tags, length_ft, length_m, pitches


def parse_fa(text: str) -> str | None:
    match = re.search(r"FA:\s*(.*?)\s*(?=Page Views:|Shared By:|Admins:|$)", text)
    if not match:
        return None
    return clean_text(match.group(1))


def parse_comment_count(soup: BeautifulSoup) -> int | None:
    count_el = soup.select_one(".comment-count")
    if count_el:
        match = re.search(r"(\d+)", count_el.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    body_text = clean_text(soup.get_text(" ", strip=True))
    match = re.search(r"(\d+)\s+Comments", body_text)
    if match:
        return int(match.group(1))
    return None


def parse_photo_refs(
    soup: BeautifulSoup,
    *,
    page_url: str,
    parent_type: str,
    parent_id: str,
) -> list[PhotoRecord]:
    photos: dict[str, PhotoRecord] = {}
    for anchor in soup.select('a[href*="/photo/"]'):
        href = absolute_url(page_url, anchor.get("href"))
        if not href:
            continue
        photo_page_url = canonical_page_url(href)
        photo_id = extract_object_id(photo_page_url, "photo")
        if not photo_id:
            continue

        photo = photos.get(photo_id)
        if photo is None:
            photo = PhotoRecord(
                photo_id=photo_id,
                parent_type=parent_type,
                parent_id=parent_id,
                parent_url=page_url,
                photo_page_url=photo_page_url,
            )
            photos[photo_id] = photo

        title = clean_text(anchor.get_text(" ", strip=True))
        if title and not photo.title:
            photo.title = title

        img = anchor.find("img")
        if img is None:
            card = anchor.find_parent(class_=re.compile(r"photo-card|card-with-photo"))
            img = card.find("img") if isinstance(card, Tag) else None
        if img is not None and not photo.thumbnail_url:
            thumbnail = img.get("data-original") or img.get("data-src") or img.get("src")
            if thumbnail:
                photo.thumbnail_url = absolute_url(page_url, thumbnail)

    return list(photos.values())


def parse_photo_page(html: str, photo: PhotoRecord) -> PhotoRecord:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one('meta[property="og:image"]')
    if meta and meta.get("content"):
        photo.image_url = absolute_url(photo.photo_page_url, meta.get("content"))
    if not photo.title:
        photo.title = clean_text((soup.select_one("title") or soup.select_one("h1")).get_text(" ", strip=True))
    img = soup.select_one("img.main-photo")
    if img is not None and not photo.thumbnail_url:
        src = img.get("src")
        if src:
            photo.thumbnail_url = absolute_url(photo.photo_page_url, src)
    return photo


def parse_comments_fragment(
    html: str,
    *,
    parent_type: str,
    parent_id: str,
    parent_url: str,
) -> tuple[list[CommentRecord], bool, int | None]:
    soup = BeautifulSoup(html, "html.parser")
    comments: list[CommentRecord] = []
    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False) or row.find_all("td")
        if len(cells) < 2:
            continue
        author_cell = cells[0]
        body_cell = cells[-1]

        author_link = author_cell.select_one('a[href*="/user/"]') or body_cell.select_one('a[href*="/user/"]')
        author_name = clean_text(author_link.get_text(" ", strip=True)) if author_link else None
        author_url = absolute_url(parent_url, author_link.get("href")) if author_link else None

        author_meta = clean_text(author_cell.get_text(" ", strip=True))
        if author_name and author_meta.startswith(author_name):
            author_meta = clean_text(author_meta[len(author_name) :])
        if not author_meta:
            author_meta = None

        date_link = body_cell.find("a", href=re.compile(r"#Comment-"))
        posted_at = clean_text(date_link.get_text(" ", strip=True)) if date_link else None
        comment_url = absolute_url(parent_url, date_link.get("href")) if date_link else None
        comment_id = None
        if comment_url:
            match = re.search(r"Comment-(\d+)", comment_url)
            if match:
                comment_id = match.group(1)

        body_block = next(
            (
                child
                for child in body_cell.find_all(["div", "p"], recursive=False)
                if child.find("a", href=re.compile(r"#Comment-"))
            ),
            None,
        )
        body_text = clean_text(body_block.get_text(" ", strip=True)) if body_block else clean_text(body_cell.get_text(" ", strip=True))
        if posted_at and body_text.endswith(posted_at):
            body_text = clean_text(body_text.removesuffix(posted_at))

        beta_match = re.search(r"Beta:\s*(\d+)", body_cell.get_text(" ", strip=True))
        beta_count = int(beta_match.group(1)) if beta_match else None

        comments.append(
            CommentRecord(
                comment_id=comment_id,
                parent_type=parent_type,
                parent_id=parent_id,
                parent_url=parent_url,
                author_name=author_name,
                author_url=author_url,
                author_meta=author_meta,
                body=body_text or None,
                posted_at=posted_at,
                comment_url=comment_url,
                beta_count=beta_count,
            )
        )

    more_button = soup.select_one(".show-more-comments-trigger")
    truncated = more_button is not None
    remaining_comment_count = None
    if more_button:
        match = re.search(r"Show\s+(\d+)\s+More Comments", more_button.get_text(" ", strip=True))
        if match:
            remaining_comment_count = int(match.group(1))

    return comments, truncated, remaining_comment_count


def parse_area_page(html: str, page_url: str) -> AreaRecord:
    soup = BeautifulSoup(html, "html.parser")
    area_id = extract_object_id(page_url, "area")
    if not area_id:
        raise ValueError(f"Could not extract area id from {page_url}")

    breadcrumbs, breadcrumb_urls = parse_breadcrumbs(soup)
    details_raw = extract_details_rows(soup)
    details_text = extract_details_text(soup)
    latitude, longitude = parse_geo_from_schema(soup)
    if latitude is None or longitude is None:
        latitude, longitude = parse_float_pair(details_text)
    if latitude is None or longitude is None:
        latitude, longitude = parse_onx_coordinates(soup)

    elevation_ft, elevation_m = parse_elevation(details_text)
    page_views_total, page_views_monthly = parse_page_views(details_text)
    shared_by, shared_date = parse_shared_by(details_text)

    current_url = canonical_page_url(page_url)
    child_area_urls = parse_child_area_urls(
        soup,
        page_url=current_url,
        current_area_id=area_id,
        breadcrumb_urls=breadcrumb_urls,
    )

    route_urls = []
    for link in soup.select('a[href*="/route/"]'):
        href = absolute_url(page_url, link.get("href"))
        if not href or "/route/stats/" in href:
            continue
        href = canonical_page_url(href)
        if not is_route_page_url(href):
            continue
        route_urls.append(href)

    return AreaRecord(
        area_id=area_id,
        url=current_url,
        name=clean_text((soup.select_one("h1") or soup.select_one("title")).get_text(" ", strip=True)),
        breadcrumbs=breadcrumbs,
        breadcrumb_urls=breadcrumb_urls,
        description=extract_section_text(soup, "Description"),
        getting_there=extract_section_text(soup, "Getting There"),
        latitude=latitude,
        longitude=longitude,
        elevation_ft=elevation_ft,
        elevation_m=elevation_m,
        page_views_total=page_views_total,
        page_views_monthly=page_views_monthly,
        shared_by=shared_by,
        shared_date=shared_date,
        details_raw=details_raw,
        child_area_urls=child_area_urls,
        route_urls=dedupe_keep_order(route_urls),
        photos=parse_photo_refs(soup, page_url=current_url, parent_type="area", parent_id=area_id),
        comment_count=parse_comment_count(soup),
    )


def parse_route_page(html: str, page_url: str) -> RouteRecord:
    soup = BeautifulSoup(html, "html.parser")
    route_id = extract_object_id(page_url, "route")
    if not route_id:
        raise ValueError(f"Could not extract route id from {page_url}")

    breadcrumbs, breadcrumb_urls = parse_breadcrumbs(soup)
    details_raw = extract_details_rows(soup)
    details_text = extract_details_text(soup)
    latitude, longitude = parse_geo_from_schema(soup)
    if latitude is None or longitude is None:
        latitude, longitude = parse_float_pair(details_text)
    if latitude is None or longitude is None:
        latitude, longitude = parse_onx_coordinates(soup)

    route_type_raw, type_tags, length_ft, length_m, pitches = parse_route_specs(details_text)
    page_views_total, page_views_monthly = parse_page_views(details_text)
    shared_by, shared_date = parse_shared_by(details_text)
    stars_avg, vote_count = parse_rating(soup)

    grade_el = soup.select_one("h2.inline-block") or soup.select_one(".rateYDS")
    yds_el = soup.select_one(".rateYDS")

    return RouteRecord(
        route_id=route_id,
        url=canonical_page_url(page_url),
        name=clean_text((soup.select_one("h1") or soup.select_one("title")).get_text(" ", strip=True)),
        breadcrumbs=breadcrumbs,
        breadcrumb_urls=breadcrumb_urls,
        grade_raw=clean_text(grade_el.get_text(" ", strip=True)) if grade_el else None,
        yds_grade=clean_text(yds_el.get_text(" ", strip=True)) if yds_el else None,
        route_type_raw=route_type_raw,
        type_tags=type_tags,
        length_ft=length_ft,
        length_m=length_m,
        pitches=pitches,
        latitude=latitude,
        longitude=longitude,
        stars_avg=stars_avg,
        vote_count=vote_count,
        fa=parse_fa(details_text),
        page_views_total=page_views_total,
        page_views_monthly=page_views_monthly,
        shared_by=shared_by,
        shared_date=shared_date,
        description=extract_section_text(soup, "Description"),
        protection=extract_section_text(soup, "Protection"),
        location=extract_section_text(soup, "Location"),
        details_raw=details_raw,
        photos=parse_photo_refs(soup, page_url=canonical_page_url(page_url), parent_type="route", parent_id=route_id),
        comment_count=parse_comment_count(soup),
    )
