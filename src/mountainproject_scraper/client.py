from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import requests


@dataclass(slots=True)
class CachedTextResponse:
    url: str
    text: str
    from_cache: bool


class AuthenticationError(RuntimeError):
    pass


class RateLimitAbortError(RuntimeError):
    pass


def _cache_key(
    url: str,
    params: dict[str, Any] | None = None,
    namespace: str = "default",
) -> str:
    payload = json.dumps(
        {"namespace": namespace, "url": url, "params": params or {}},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RequestRateLimiter:
    def __init__(self, delay_seconds: float, *, rate_limit_pause_seconds: float = 90.0) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.rate_limit_pause_seconds = max(0.0, rate_limit_pause_seconds)
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0
        self._consecutive_rate_limit_cycles = 0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled_at = max(now, self._next_request_at)
            self._next_request_at = scheduled_at + self.delay_seconds

        wait_seconds = scheduled_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def note_rate_limit_response(self) -> tuple[float, int, bool]:
        now = time.monotonic()
        with self._lock:
            cooldown_active = now < self._cooldown_until
            self._cooldown_until = max(self._cooldown_until, now + self.rate_limit_pause_seconds)
            self._next_request_at = max(self._next_request_at, self._cooldown_until)
            if not cooldown_active:
                self._consecutive_rate_limit_cycles += 1
            return self._cooldown_until - now, self._consecutive_rate_limit_cycles, not cooldown_active

    def note_success(self) -> None:
        with self._lock:
            self._consecutive_rate_limit_cycles = 0


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def load_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> str | None:
        path = self.root / f"{_cache_key(url, params, namespace)}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("text")

    def store_text(
        self,
        url: str,
        text: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> None:
        path = self.root / f"{_cache_key(url, params, namespace)}.json"
        payload = {"namespace": namespace, "url": url, "params": params, "text": text}
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class MemoryCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}

    def load_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> str | None:
        key = _cache_key(url, params, namespace)
        with self._lock:
            return self._data.get(key)

    def store_text(
        self,
        url: str,
        text: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> None:
        key = _cache_key(url, params, namespace)
        with self._lock:
            self._data[key] = text


class NullCache:
    def load_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> str | None:
        return None

    def store_text(
        self,
        url: str,
        text: str,
        params: dict[str, Any] | None = None,
        namespace: str = "default",
    ) -> None:
        return None


def create_text_cache(cache_mode: str, cache_dir: Path):
    if cache_mode == "persistent":
        return DiskCache(cache_dir)
    if cache_mode == "ephemeral":
        return MemoryCache()
    if cache_mode == "disabled":
        return NullCache()
    raise ValueError(f"Unsupported cache mode: {cache_mode}")


class MountainProjectClient:
    def __init__(
        self,
        *,
        delay_seconds: float,
        cache_dir: Path,
        user_agent: str,
        cookies: list[str] | None = None,
        login_email: str | None = None,
        login_password: str | None = None,
        timeout_seconds: float = 30.0,
        rate_limiter: RequestRateLimiter | None = None,
        cache_mode: str = "ephemeral",
        cache_backend: Any | None = None,
        cookie_jar: requests.cookies.RequestsCookieJar | None = None,
        cache_namespace: str | None = None,
        auth_mode: str | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.timeout_seconds = timeout_seconds
        self.cache_dir = cache_dir
        self.cache_mode = cache_mode
        self.user_agent = user_agent
        self.cookies = list(cookies or [])
        self.login_email = login_email
        self.rate_limiter = rate_limiter or RequestRateLimiter(self.delay_seconds)
        self.cache = cache_backend or create_text_cache(cache_mode, cache_dir)
        has_auth = bool(self.cookies or cookie_jar or login_email)
        self.cache_namespace = cache_namespace or ("authenticated" if has_auth else "anonymous")
        self.auth_mode = auth_mode or ("login" if login_email else ("cookie" if has_auth else "anonymous"))
        self.max_retries = 5
        self.log = log or (lambda _message: None)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._apply_cookies(self.cookies)
        if cookie_jar is not None:
            self.session.cookies.update(cookie_jar)
        if login_email or login_password:
            if not (login_email and login_password):
                raise ValueError("Both login_email and login_password are required for authenticated login.")
            self._login(login_email, login_password)

    def clone(self) -> "MountainProjectClient":
        return MountainProjectClient(
            delay_seconds=self.delay_seconds,
            cache_dir=self.cache_dir,
            user_agent=self.user_agent,
            timeout_seconds=self.timeout_seconds,
            rate_limiter=self.rate_limiter,
            cache_mode=self.cache_mode,
            cache_backend=self.cache,
            cookie_jar=self.session.cookies.copy(),
            cache_namespace=self.cache_namespace,
            auth_mode=self.auth_mode,
            log=self.log,
        )

    def fetch_text(self, url: str, params: dict[str, Any] | None = None) -> CachedTextResponse:
        cached = self.cache.load_text(url, params, namespace=self.cache_namespace)
        if cached is not None:
            return CachedTextResponse(url=url, text=cached, from_cache=True)

        response = self._request_with_retry("get", url, params=params)
        response.raise_for_status()
        self.cache.store_text(response.url, response.text, params, namespace=self.cache_namespace)
        return CachedTextResponse(url=response.url, text=response.text, from_cache=False)

    def download_file(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return destination

        response = self._request_with_retry("get", url)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return destination

    def _apply_cookies(self, cookie_pairs: list[str]) -> None:
        for cookie in cookie_pairs:
            name, separator, value = cookie.partition("=")
            if not separator:
                raise ValueError(f"Invalid cookie value: {cookie!r}. Expected name=value.")
            self.session.cookies.set(name.strip(), value.strip(), domain="www.mountainproject.com")

    def _login(self, email: str, password: str) -> None:
        login_page_url = "https://www.mountainproject.com/auth/login"
        login_action_url = urljoin(login_page_url, "/auth/login/email")

        login_page_response = self._request_with_retry("get", login_page_url)
        login_page_response.raise_for_status()
        token = self._extract_login_token(login_page_response.text)
        if not token:
            raise AuthenticationError("Could not find the Mountain Project login token.")

        response = self._request_with_retry(
            "post",
            login_action_url,
            data={"email": email, "pass": password, "_token": token},
            headers={"Referer": login_page_url},
        )
        response.raise_for_status()

        if self._looks_like_login_failure(response.text, response.url):
            raise AuthenticationError(
                "Mountain Project login failed. Check the email/password or use --cookie with an active session."
            )

        self.cache_namespace = "authenticated"
        self.auth_mode = "login"

    def _extract_login_token(self, html: str) -> str | None:
        match = re.search(r'name="_token"\s+value="([^"]+)"', html)
        if match:
            return match.group(1)
        return None

    def _looks_like_login_failure(self, html: str, url: str) -> bool:
        path = urlsplit(url).path
        if path.startswith("/auth/login") and 'name="email"' in html and 'name="pass"' in html:
            return True
        return False

    def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_response: requests.Response | None = None
        transient_attempt = 0
        while True:
            self.rate_limiter.acquire()
            try:
                response = self.session.request(method, url, timeout=self.timeout_seconds, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if transient_attempt >= self.max_retries:
                    raise exc
                transient_attempt += 1
                retry_after = min(30.0, 1.0 * (2 ** (transient_attempt - 1)))
                self.log(
                    f"Retrying request after transport error: {url} "
                    f"({type(exc).__name__}, attempt {transient_attempt}/{self.max_retries}, waiting {retry_after:.1f}s)"
                )
                time.sleep(retry_after)
                continue
            last_response = response

            if 200 <= response.status_code < 400:
                self.rate_limiter.note_success()
                return response

            if response.status_code == 429:
                wait_seconds, consecutive_cycles, started_new_cycle = self.rate_limiter.note_rate_limit_response()
                if consecutive_cycles >= 2 and started_new_cycle:
                    raise RateLimitAbortError(
                        "Received HTTP 429 again before any successful request completed. "
                        f"Aborting scrape after one {self.rate_limiter.rate_limit_pause_seconds:.0f}-second global pause. "
                        f"Last URL: {url}"
                    )
                if started_new_cycle:
                    self.log(
                        f"Received HTTP 429: pausing all threads for {wait_seconds:.1f}s before retrying {url}"
                    )
                else:
                    self.log(
                        "Received additional HTTP 429 during the active cooldown: "
                        f"extending the global pause to {wait_seconds:.1f}s from the latest failure for {url}"
                    )
                continue

            if response.status_code not in {500, 502, 503, 504}:
                return response

            if transient_attempt >= self.max_retries:
                return response

            transient_attempt += 1

            retry_after = self._retry_after_seconds(response)
            if retry_after is None:
                retry_after = min(30.0, 1.0 * (2 ** (transient_attempt - 1)))
            self.log(
                f"Retrying request after HTTP {response.status_code}: {url} "
                f"(attempt {transient_attempt}/{self.max_retries}, waiting {retry_after:.1f}s)"
            )
            time.sleep(retry_after)

        return last_response if last_response is not None else self.session.request(method, url, timeout=self.timeout_seconds, **kwargs)

    def _retry_after_seconds(self, response: requests.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return max(0.0, float(header))
        except ValueError:
            return None
