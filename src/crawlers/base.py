"""Low-level polite HTTP fetching: shared client, per-host rate limiting,
robots.txt compliance, conditional requests (ETag/Last-Modified) and a small
on-disk cache of robots rules.

This module NEVER attempts to bypass captchas, logins, paywalls or any access
control. A 401/403/429 is respected, logged and surfaced — not worked around.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from ..config.settings import get_settings
from ..utils.hashing import domain_of
from ..utils.logging import get_logger

log = get_logger("crawler")


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str = ""
    content: bytes = b""
    headers: dict = field(default_factory=dict)
    ok: bool = False
    from_cache: bool = False
    error: str | None = None
    blocked_by_robots: bool = False


class RobotsCache:
    """Fetch + cache robots.txt per host and answer allow/deny + crawl-delay."""

    def __init__(self, user_agent: str, respect: bool = True):
        self.user_agent = user_agent
        self.respect = respect
        self._parsers: dict[str, robotparser.RobotFileParser] = {}
        self._lock = threading.Lock()

    def _parser_for(self, url: str) -> robotparser.RobotFileParser | None:
        host = urlparse(url).netloc
        if not host:
            return None
        with self._lock:
            if host in self._parsers:
                return self._parsers[host]
        rp = robotparser.RobotFileParser()
        robots_url = urljoin(f"{urlparse(url).scheme}://{host}", "/robots.txt")
        try:
            resp = httpx.get(robots_url, timeout=10,
                             headers={"User-Agent": self.user_agent},
                             follow_redirects=True)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # no robots => allow
        except httpx.HTTPError:
            rp.parse([])  # unreachable robots => be permissive but polite
        with self._lock:
            self._parsers[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect:
            return True
        rp = self._parser_for(url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay(self, url: str) -> float | None:
        rp = self._parser_for(url)
        if rp is None:
            return None
        try:
            d = rp.crawl_delay(self.user_agent)
            return float(d) if d else None
        except Exception:
            return None


class _RateLimiter:
    """Enforce a minimum delay between requests to the same host."""

    def __init__(self, default_delay: float):
        self.default_delay = default_delay
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str, delay: float | None = None) -> None:
        host = domain_of(url)
        d = max(self.default_delay, delay or 0)
        with self._lock:
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            sleep_for = last + d - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last[host] = time.monotonic()


class HttpFetcher:
    """Synchronous, polite fetcher. One instance is shared per scan."""

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.robots = RobotsCache(self.settings.user_agent, self.settings.respect_robots)
        self.rate = _RateLimiter(self.settings.crawl_delay_seconds)
        # Conditional-request memory: url -> (etag, last_modified)
        self._conditional: dict[str, tuple[str | None, str | None]] = {}
        self._client = httpx.Client(
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en,de;q=0.8,fr;q=0.6,it;q=0.5",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fetch(self, url: str, *, conditional: bool = True) -> FetchResult:
        if not self.robots.allowed(url):
            log.info("robots.txt disallows %s", url)
            return FetchResult(url=url, status_code=0, blocked_by_robots=True,
                               error="blocked_by_robots")
        self.rate.wait(url, self.robots.crawl_delay(url))

        headers: dict[str, str] = {}
        if conditional and url in self._conditional:
            etag, last_mod = self._conditional[url]
            if etag:
                headers["If-None-Match"] = etag
            if last_mod:
                headers["If-Modified-Since"] = last_mod

        try:
            resp = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("fetch error %s: %s", url, exc)
            return FetchResult(url=url, status_code=0, error=str(exc))

        # Remember validators for next time.
        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")
        if etag or last_mod:
            self._conditional[url] = (etag, last_mod)

        if resp.status_code == 304:
            return FetchResult(url=url, status_code=304, ok=True, from_cache=True,
                               headers=dict(resp.headers))

        ok = 200 <= resp.status_code < 300
        if resp.status_code in (401, 403, 429):
            log.info("access-limited %s -> HTTP %s (respected, not bypassed)",
                     url, resp.status_code)
        return FetchResult(
            url=url,
            status_code=resp.status_code,
            text=resp.text if ok else "",
            content=resp.content if ok else b"",
            headers=dict(resp.headers),
            ok=ok,
            error=None if ok else f"HTTP {resp.status_code}",
        )


@dataclass
class CrawlContext:
    """Shared state passed to per-source crawlers during a scan."""

    fetcher: HttpFetcher
    max_pages: int
    pages_checked: int = 0
    errors: list[str] = field(default_factory=list)
