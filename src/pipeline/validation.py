"""Live source validation and source-quality audit.

`validate_sources` gives every source a GROUND-TRUTH status from a real fetch
(never "WORKING" just because it's on a seed list). `audit_sources` reports, per
country, which source *categories* are missing so long-tail expansion can be
targeted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config.countries import COUNTRIES
from ..crawlers.base import CrawlContext, HttpFetcher
from ..crawlers.http_crawler import crawl_source
from ..database.base import session_scope
from ..database.writer import run_write
from ..models.enums import SourceLiveStatus
from ..models.source import Source
from ..network.validate import AccessStatus, NetworkValidator
from ..utils.logging import get_logger

log = get_logger("pipeline.validation")

_ACCESS_TO_LIVE = {
    AccessStatus.BLOCKED.value: SourceLiveStatus.BLOCKED.value,
    AccessStatus.RATE_LIMITED.value: SourceLiveStatus.RATE_LIMITED.value,
    AccessStatus.ROBOTS_DISALLOWED.value: SourceLiveStatus.ROBOTS_DISALLOWED.value,
    AccessStatus.JS_REQUIRED.value: SourceLiveStatus.JS_REQUIRED.value,
    AccessStatus.LOGIN_REQUIRED.value: SourceLiveStatus.LOGIN_REQUIRED.value,
    AccessStatus.ERROR.value: SourceLiveStatus.UNKNOWN.value,
}


def validate_sources(limit: int | None = None, country: str | None = None) -> dict:
    """Fetch each active source once, classify access, and (if reachable) try a
    real extraction to decide WORKING vs PARTIALLY_WORKING vs PARSER_BROKEN.

    Concurrency-safe: sources are read once, then each is probed (network) with
    NO DB txn open, and its result is written in a short serialised write."""
    validator = NetworkValidator()
    settings = validator.settings
    counts: dict[str, int] = {}
    checked = 0

    # Read the source list once (detached), then release the read session.
    with session_scope() as session:
        q = session.query(Source).filter(Source.active.is_(True))
        if country:
            q = q.filter(Source.country == country.upper())
        sources = q.order_by(Source.priority.desc()).all()
        if limit:
            sources = sources[:limit]
        for s in sources:
            session.expunge(s)

    with HttpFetcher(settings) as fetcher:
        for src in sources:
            url = src.search_url or src.base_url
            # NETWORK: classify + optional extraction (no DB txn open).
            probe = validator.classify_source(src.domain, url)
            live = _ACCESS_TO_LIVE.get(probe.status)
            detail = f"{probe.status} {probe.http_status or ''} {probe.detail}".strip()

            if probe.status == AccessStatus.ACCESSIBLE.value:
                ctx = CrawlContext(fetcher=fetcher, max_pages=3)
                try:
                    candidates, _ = crawl_source(ctx, src)
                except Exception as exc:
                    candidates = []
                    detail = f"crawl error: {exc}"[:290]
                if candidates:
                    live = SourceLiveStatus.WORKING.value
                    detail = f"extracted {len(candidates)} candidate(s)"
                else:
                    live = (SourceLiveStatus.PARSER_BROKEN.value
                            if (src.typical_result_count or 0) >= 3
                            else SourceLiveStatus.PARTIALLY_WORKING.value)

            requires_browser = probe.status == AccessStatus.JS_REQUIRED.value
            if probe.status == AccessStatus.ERROR.value and not validator.check_dns(src.domain):
                live = SourceLiveStatus.DEAD.value
            live = live or SourceLiveStatus.UNKNOWN.value

            # SHORT WRITE: persist this source's live status.
            run_write(lambda s, sid=src.id, lv=live, dt=detail,
                      rb=requires_browser: _write_live_status(s, sid, lv, dt, rb),
                      label="validate_source", swallow=True)
            counts[live] = counts.get(live, 0) + 1
            checked += 1

    log.info("Validated %d sources: %s", checked, counts)
    return {"checked": checked, "by_status": counts}


def _write_live_status(session, source_id, live, detail, requires_browser):
    src = session.get(Source, source_id)
    if not src:
        return
    src.live_status = live
    src.live_status_detail = detail[:300]
    src.live_checked_at = datetime.now(timezone.utc)
    if requires_browser:
        src.requires_browser = True


# Categories we want present for good coverage in each country.
_EXPECTED_CATEGORIES = {
    "major_marketplace": "national marketplace",
    "classifieds": "classifieds",
    "dealer": "independent dealers",
    "suzuki_dealer": "Suzuki dealers",
    "youngtimer_dealer": "youngtimer/classic",
    "forum": "forums",
    "club": "clubs",
}


def audit_sources() -> list[dict]:
    """Per-country gap analysis: which source categories are missing/thin."""
    rows: list[dict] = []
    with session_scope() as session:
        for code, country in COUNTRIES.items():
            present: dict[str, int] = {}
            for stype, in session.query(Source.source_type).filter(
                    Source.country == code).all():
                present[stype] = present.get(stype, 0) + 1
            working = session.query(Source).filter(
                Source.country == code,
                Source.live_status == SourceLiveStatus.WORKING.value).count()
            missing = [label for cat, label in _EXPECTED_CATEGORIES.items()
                       if present.get(cat, 0) == 0]
            rows.append({
                "country": country.name_en,
                "code": code,
                "total_sources": sum(present.values()),
                "working": working,
                "by_type": present,
                "missing_categories": missing,
            })
    rows.sort(key=lambda r: (len(r["missing_categories"]), -r["total_sources"]),
              reverse=True)
    return rows
