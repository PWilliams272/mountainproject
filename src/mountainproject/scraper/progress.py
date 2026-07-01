from __future__ import annotations

from time import perf_counter

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class NullProgressReporter:
    def __init__(self) -> None:
        self._started_at = perf_counter()

    def __enter__(self) -> "NullProgressReporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @property
    def elapsed_seconds(self) -> float:
        return perf_counter() - self._started_at

    def set_status(self, message: str) -> None:
        return None

    def register_areas(self, count: int) -> None:
        return None

    def complete_area(self, name: str | None = None) -> None:
        return None

    def register_routes(self, count: int) -> None:
        return None

    def complete_route(self, name: str | None = None) -> None:
        return None

    def register_route_stats(self, count: int) -> None:
        return None

    def complete_route_stats(self, name: str | None = None) -> None:
        return None

    def log(self, message: str) -> None:
        return None


class RichProgressReporter:
    def __init__(self, *, console: Console | None = None) -> None:
        self.console = console or Console()
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )
        self._started_at = perf_counter()
        self._areas_total = 0
        self._routes_total = 0
        self._route_stats_total = 0
        self._status_task_id: TaskID | None = None
        self._areas_task_id: TaskID | None = None
        self._routes_task_id: TaskID | None = None
        self._route_stats_task_id: TaskID | None = None

    def __enter__(self) -> "RichProgressReporter":
        self.progress.start()
        self._status_task_id = self.progress.add_task("Preparing scrape", total=None)
        self._areas_task_id = self.progress.add_task("Areas", total=1)
        self._routes_task_id = self.progress.add_task("Routes", total=1, visible=False)
        self._route_stats_task_id = self.progress.add_task("Route stats", total=1, visible=False)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.progress.stop()

    @property
    def elapsed_seconds(self) -> float:
        return perf_counter() - self._started_at

    def set_status(self, message: str) -> None:
        self.progress.update(self._status_task_id, description=message)

    def register_areas(self, count: int) -> None:
        if count <= 0:
            return
        self._areas_total += count
        self.progress.update(self._areas_task_id, total=max(1, self._areas_total))

    def complete_area(self, name: str | None = None) -> None:
        description = "Areas"
        if name:
            description = f"Areas ({name})"
        self.progress.advance(self._areas_task_id, 1)
        self.progress.update(self._areas_task_id, description=description)

    def register_routes(self, count: int) -> None:
        if count <= 0:
            return
        self._routes_total += count
        self.progress.update(
            self._routes_task_id,
            total=max(1, self._routes_total),
            visible=True,
        )

    def complete_route(self, name: str | None = None) -> None:
        description = "Routes"
        if name:
            description = f"Routes ({name})"
        self.progress.advance(self._routes_task_id, 1)
        self.progress.update(self._routes_task_id, description=description)

    def register_route_stats(self, count: int) -> None:
        if count <= 0:
            return
        self._route_stats_total += count
        self.progress.update(
            self._route_stats_task_id,
            total=max(1, self._route_stats_total),
            visible=True,
        )

    def complete_route_stats(self, name: str | None = None) -> None:
        description = "Route stats"
        if name:
            description = f"Route stats ({name})"
        self.progress.advance(self._route_stats_task_id, 1)
        self.progress.update(self._route_stats_task_id, description=description)

    def log(self, message: str) -> None:
        self.console.log(message)


def create_progress_reporter(enabled: bool):
    if enabled:
        return RichProgressReporter()
    return NullProgressReporter()