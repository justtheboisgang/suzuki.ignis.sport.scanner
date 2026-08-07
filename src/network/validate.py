"""Network validation.

Transparently *tests and classifies* connectivity — it never bypasses any
protection. Each probed source is labelled ACCESSIBLE / BLOCKED / RATE_LIMITED /
ROBOTS_DISALLOWED / JS_REQUIRED / LOGIN_REQUIRED / ERROR so you can see, on the
real VPS, exactly what will and won't crawl.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from enum import Enum

import httpx

from ..config.settings import get_settings
from ..crawlers.base import RobotsCache
from ..utils.logging import get_logger

log = get_logger("network.validate")


class AccessStatus(str, Enum):
    ACCESSIBLE = "ACCESSIBLE"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    JS_REQUIRED = "JS_REQUIRED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    ERROR = "ERROR"


# A representative sample across regions/source types.
SAMPLE_SOURCES = [
    ("mobile.de", "https://www.mobile.de/"),
    ("autoscout24.de", "https://www.autoscout24.de/"),
    ("kleinanzeigen.de", "https://www.kleinanzeigen.de/"),
    ("willhaben.at", "https://www.willhaben.at/"),
    ("otomoto.pl", "https://www.otomoto.pl/"),
    ("leboncoin.fr", "https://www.leboncoin.fr/"),
    ("subito.it", "https://www.subito.it/"),
    ("coches.net", "https://www.coches.net/"),
    ("marktplaats.nl", "https://www.marktplaats.nl/"),
    ("standvirtual.com", "https://www.standvirtual.com/"),
    ("sauto.cz", "https://www.sauto.cz/"),
    ("carandclassic.com", "https://www.carandclassic.com/"),
]

_CAPTCHA_MARKERS = ("captcha", "are you a robot", "cf-browser-verification",
                    "just a moment", "attention required", "cloudflare")
_LOGIN_MARKERS = ("please log in", "sign in to continue", "login required",
                  "anmelden erforderlich")
_JS_MARKERS = ("enable javascript", "please enable js", "you need to enable "
               "javascript", "<noscript>")


@dataclass
class ProbeResult:
    name: str
    status: str
    http_status: int | None = None
    detail: str = ""


@dataclass
class NetworkReport:
    checks: dict = field(default_factory=dict)      # name -> "OK"/"NOT CONFIGURED"/...
    sources: list = field(default_factory=list)     # list[ProbeResult]
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "checks": self.checks,
            "sources": [p.__dict__ for p in self.sources],
            "summary": self.summary,
        }


class NetworkValidator:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.robots = RobotsCache(self.settings.user_agent, respect=True)

    # --- basic connectivity ---------------------------------------------
    def check_dns(self, host: str = "example.com") -> bool:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            return False

    def check_http(self, url: str = "https://example.com") -> tuple[bool, str]:
        try:
            r = httpx.get(url, timeout=15,
                          headers={"User-Agent": self.settings.user_agent},
                          follow_redirects=True)
            return (200 <= r.status_code < 400, f"HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            return (False, type(exc).__name__)

    # --- API connectivity -----------------------------------------------
    def check_anthropic(self) -> tuple[str, str]:
        if not self.settings.anthropic_api_key:
            return ("NOT CONFIGURED", "set ANTHROPIC_API_KEY")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            # A minimal, cheap call to prove auth + connectivity.
            client.models.list()
            return ("OK", "auth + connectivity OK")
        except Exception as exc:  # pragma: no cover - needs network+key
            return ("ERROR", f"{type(exc).__name__}: {str(exc)[:120]}")

    def check_brave(self) -> tuple[str, str]:
        key = self.settings.brave_key
        if not key:
            return ("NOT CONFIGURED", "set BRAVE_SEARCH_API_KEY")
        try:
            r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                          headers={"X-Subscription-Token": key,
                                   "Accept": "application/json"},
                          params={"q": "Suzuki Ignis Sport", "count": 1}, timeout=20)
            if r.status_code == 200:
                return ("OK", "query OK")
            if r.status_code in (401, 403):
                return ("ERROR", f"auth failed HTTP {r.status_code}")
            if r.status_code == 429:
                return ("RATE_LIMITED", "HTTP 429")
            return ("ERROR", f"HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            return ("ERROR", type(exc).__name__)

    def check_serpapi(self) -> tuple[str, str]:
        key = self.settings.serpapi_key_resolved
        if not key:
            return ("NOT CONFIGURED", "set SERPAPI_API_KEY")
        try:
            r = httpx.get("https://serpapi.com/search.json",
                          params={"engine": "google", "q": "Suzuki Ignis Sport",
                                  "num": 1, "api_key": key}, timeout=25)
            if r.status_code == 200:
                return ("OK", "query OK")
            return ("ERROR", f"HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            return ("ERROR", type(exc).__name__)

    def check_playwright(self) -> tuple[str, str]:
        try:
            import playwright  # noqa: F401
        except Exception:
            return ("NOT INSTALLED", "pip install playwright && playwright install chromium")
        import os
        path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if path and os.path.isdir(path):
            return ("OK", f"browsers at {path}")
        return ("INSTALLED", "playwright present; run 'playwright install chromium'")

    # --- source probing --------------------------------------------------
    def classify_source(self, name: str, url: str) -> ProbeResult:
        # robots first — respect it.
        try:
            if not self.robots.allowed(url):
                return ProbeResult(name, AccessStatus.ROBOTS_DISALLOWED.value,
                                   detail="robots.txt disallows our UA")
        except Exception:
            pass
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True,
                          headers={"User-Agent": self.settings.user_agent,
                                   "Accept-Language": "en,de;q=0.8"})
        except httpx.HTTPError as exc:
            return ProbeResult(name, AccessStatus.ERROR.value, detail=type(exc).__name__)

        body = (r.text or "").lower()[:8000]
        if r.status_code == 429:
            return ProbeResult(name, AccessStatus.RATE_LIMITED.value, r.status_code)
        if r.status_code == 401:
            return ProbeResult(name, AccessStatus.LOGIN_REQUIRED.value, r.status_code)
        if r.status_code == 403:
            status = AccessStatus.BLOCKED.value
            if any(m in body for m in _CAPTCHA_MARKERS):
                status = AccessStatus.BLOCKED.value
            return ProbeResult(name, status, r.status_code, "forbidden / anti-bot")
        if 200 <= r.status_code < 300:
            if any(m in body for m in _CAPTCHA_MARKERS):
                return ProbeResult(name, AccessStatus.BLOCKED.value, r.status_code,
                                   "captcha/anti-bot page")
            if any(m in body for m in _LOGIN_MARKERS):
                return ProbeResult(name, AccessStatus.LOGIN_REQUIRED.value, r.status_code)
            if len(body) < 1500 and any(m in body for m in _JS_MARKERS):
                return ProbeResult(name, AccessStatus.JS_REQUIRED.value, r.status_code)
            return ProbeResult(name, AccessStatus.ACCESSIBLE.value, r.status_code)
        return ProbeResult(name, AccessStatus.ERROR.value, r.status_code,
                           f"HTTP {r.status_code}")

    # --- orchestration ---------------------------------------------------
    def run(self, probe_sources: bool = True) -> NetworkReport:
        rep = NetworkReport()
        dns_ok = self.check_dns()
        http_ok, http_detail = self.check_http()
        rep.checks["Internet"] = "OK" if http_ok else "FAILED"
        rep.checks["DNS"] = "OK" if dns_ok else "FAILED"
        rep.checks["HTTPS"] = "OK" if http_ok else f"FAILED ({http_detail})"
        rep.checks["Anthropic"] = " / ".join(self.check_anthropic())
        rep.checks["Brave Search"] = " / ".join(self.check_brave())
        rep.checks["SerpApi"] = " / ".join(self.check_serpapi())
        rep.checks["Playwright/Chromium"] = " / ".join(self.check_playwright())

        if probe_sources:
            for name, url in SAMPLE_SOURCES:
                rep.sources.append(self.classify_source(name, url))

        counts: dict[str, int] = {}
        for p in rep.sources:
            counts[p.status] = counts.get(p.status, 0) + 1
        rep.summary = {
            "sources_probed": len(rep.sources),
            "accessible": counts.get(AccessStatus.ACCESSIBLE.value, 0),
            "by_status": counts,
            "internet_ok": http_ok and dns_ok,
        }
        return rep


def run_network_test(probe_sources: bool = True) -> dict:
    """Run validation and pretty-print a human report; return the raw dict."""
    rep = NetworkValidator().run(probe_sources=probe_sources)
    print("=" * 52)
    print("NETWORK VALIDATION")
    print("=" * 52)
    for name, status in rep.checks.items():
        print(f"  {name:<20} {status}")
    if rep.sources:
        print("\n  European source sample:")
        for p in rep.sources:
            code = f"HTTP {p.http_status}" if p.http_status else ""
            print(f"    {p.name:<22} {p.status:<18} {code} {p.detail}")
    s = rep.summary
    print(f"\n  Internet OK: {s.get('internet_ok')}  |  "
          f"accessible sources: {s.get('accessible')}/{s.get('sources_probed')}")
    print("=" * 52)
    return rep.as_dict()
