from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
from typing import Any

from .client import MountainProjectClient
from .route_stats import parse_route_stats_bundle
from ..domain.models import RouteRecord


class RouteStatsFetcher:
    def __init__(self, client: MountainProjectClient, *, workers: int = 1) -> None:
        self.client = client
        self.workers = max(1, workers)
        self._thread_local = threading.local()

    def fetch_for_route(self, route: RouteRecord):
        base_url = f"https://www.mountainproject.com/api/v2/routes/{route.route_id}"
        endpoints = {
            "stars": f"{base_url}/stars",
            "ratings": f"{base_url}/ratings",
            "todos": f"{base_url}/todos",
            "ticks": f"{base_url}/ticks",
        }

        if self.workers == 1:
            stars_items = self._fetch_paginated_api_items(endpoints["stars"])
            rating_items = self._fetch_paginated_api_items(endpoints["ratings"])
            todo_items = self._fetch_paginated_api_items(endpoints["todos"])
            tick_items = self._fetch_paginated_api_items(endpoints["ticks"])
        else:
            max_workers = min(self.workers, len(endpoints))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                stars_future = executor.submit(self._fetch_paginated_api_items, endpoints["stars"])
                ratings_future = executor.submit(self._fetch_paginated_api_items, endpoints["ratings"])
                todos_future = executor.submit(self._fetch_paginated_api_items, endpoints["todos"])
                ticks_future = executor.submit(self._fetch_paginated_api_items, endpoints["ticks"])

                stars_items = stars_future.result()
                rating_items = ratings_future.result()
                todo_items = todos_future.result()
                tick_items = ticks_future.result()

        return parse_route_stats_bundle(
            route,
            stars_items=stars_items,
            rating_items=rating_items,
            todo_items=todo_items,
            tick_items=tick_items,
        )

    def _fetch_paginated_api_items(self, url: str, *, per_page: int = 250) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        client = self._client_for_current_thread()
        while True:
            response = client.fetch_text(url, params={"per_page": per_page, "page": page})
            payload = json.loads(response.text)
            page_items = payload.get("data") or []
            items.extend(page_items)

            current_page = payload.get("current_page") or page
            last_page = payload.get("last_page") or page
            if current_page >= last_page or not payload.get("next_page_url"):
                break
            page += 1
        return items

    def _client_for_current_thread(self) -> MountainProjectClient:
        client = getattr(self._thread_local, "client", None)
        if client is None:
            client = self.client.clone()
            self._thread_local.client = client
        return client