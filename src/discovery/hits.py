"""First-class Search-Hit pipeline.

Every relevant search result is stored (exact URL preserved) and investigated
directly:

    Brave/SerpApi hit → fetch exact URL → classify:
        LISTING           → ingest immediately (the car may be found via search!)
        DEALER_INVENTORY  → register/refresh Source with search_url = exact URL
        HOMEPAGE          → register Source (base_url) if it's a real dealer
        ARTICLE/PARTS/... → discovery-only (stored, not crawl-budgeted)
        BLOCKED/ROBOTS    → recorded transparently, never bypassed

This guarantees a poorly-indexed Ignis Sport ad that Brave found is not lost
just because our crawler didn't find it first.
"""

from __future__ import annotations

from ..database.base import session_scope
from ..database.writer import run_write
from ..models.search_hit import SearchHit
from ..models.source import Source
from ..pipeline.ingest import process_candidate
from ..utils.hashing import domain_of, normalize_url
from ..utils.logging import get_logger
from .classify import (
    MONITORABLE,
    classify_url_type,
    detect_country,
    domain_category,
    is_aggregator_domain,
)

log = get_logger("discovery.hits")

_LISTING_URL_HINTS = ("ignis", "ht81s", "/inserat", "/angebot", "/annonce",
                      "/annuncio", "/anuncio", "/detail", "/listing", "/fahrzeug")

# Discovery-value / monitorability by what the fetched URL actually turned out
# to be. DV is only high once we've CONFIRMED crawlable inventory (P6).
_DV_BY_TYPE = {
    "DEALER_INVENTORY": (75, 80),
    "LISTING": (65, 70),
    "HOMEPAGE": (55, 50),
}


def _store_hit(url, norm, dom, title, snippet, query, providers, rank, page,
               country) -> int | None:
    def _do(s):
        row = SearchHit(
            exact_url=url[:1000], normalized_url=norm[:1000], domain=dom,
            title=(title or "")[:500], snippet=(snippet or "")[:2000],
            query=query, provider="+".join(providers), rank=rank, page=page,
            country=country, processing_status="PENDING")
        s.add(row)
        s.flush()
        return row.id
    return run_write(_do, label="search_hit.create", swallow=True)


def _update_hit(hit_id, **fields):
    if hit_id is None:
        return

    def _do(s):
        row = s.get(SearchHit, hit_id)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
    run_write(_do, label="search_hit.update", swallow=True)


def _register_source(dom, cat, country_info, verdict_type, search_url,
                     query_country, query, providers, had_listings
                     ) -> tuple[int | None, bool]:
    """Create/refresh a Source. Returns (source_id, monitorable). Monitorable
    sources are active + carry a real discovery value; discovery-only sites are
    stored inactive so they never burn crawl budget (P4/P6)."""
    monitorable = cat["category"] == MONITORABLE and not is_aggregator_domain(dom)
    dv, mon_conf = _DV_BY_TYPE.get(verdict_type, (20, 10))
    if verdict_type == "DEALER_INVENTORY" and not had_listings:
        dv, mon_conf = 60, 65
    if not monitorable:
        dv, mon_conf = min(dv, 25), min(mon_conf, 15)

    # A concrete source_type is required (NOT NULL). Infer conservatively.
    inferred_type = ("dealer" if monitorable else "other")

    def _do(s):
        src = s.query(Source).filter(Source.domain == dom).first()
        if src is None:
            src = Source(domain=dom, base_url=f"https://{dom}/",
                         source_type=inferred_type,
                         discovery_method=f"search_hit:{'+'.join(providers)}",
                         discovery_query=query)
            s.add(src)
        elif not src.source_type:
            src.source_type = inferred_type
        src.source_category = cat["category"]
        src.source_relevance_confidence = cat["relevance"]
        src.monitorability_confidence = mon_conf
        src.discovery_value = dv
        src.active = monitorable
        src.manual_review = not monitorable
        if search_url and monitorable:
            src.search_url = search_url          # exact inventory/search URL!
        # Verified country from the actual page; query country is only a hint.
        if country_info["country"]:
            src.country = country_info["country"]
        if not src.discovered_for_country:
            src.discovered_for_country = query_country
        if not src.notes:
            src.notes = "; ".join(cat["reasons"])[:500]
        s.flush()
        return src.id
    source_id = run_write(_do, label="search_hit.source", swallow=True)
    return source_id, monitorable


def _load_source(source_id):
    if source_id is None:
        return None
    with session_scope() as s:
        src = s.get(Source, source_id)
        if src:
            s.expunge(src)
        return src


def process_search_hit(fetcher, url, title, snippet, query, providers, rank,
                       page, country) -> dict:
    """Investigate one relevant hit. Returns an outcome dict for counters."""
    dom = domain_of(url)
    norm = normalize_url(url)
    out = {"domain": dom, "hit_type": "UNKNOWN", "ingested": False,
           "source_registered": False, "monitorable": False, "status": "PENDING"}
    hit_id = _store_hit(url, norm, dom, title, snippet, query, providers, rank,
                        page, country)

    aggregator = is_aggregator_domain(dom)
    looks_listing = any(h in url.lower() for h in _LISTING_URL_HINTS)
    if aggregator and not looks_listing:
        out["status"] = "SKIPPED"
        _update_hit(hit_id, hit_type="AGGREGATOR", processing_status="SKIPPED",
                    note="aggregator non-listing URL")
        return out

    res = fetcher.fetch(url)
    out["http_status"] = res.status_code
    if res.blocked_by_robots:
        out["status"] = "BLOCKED"
        _update_hit(hit_id, processing_status="BLOCKED", http_status=0,
                    note="robots.txt disallows")
        return out
    if not res.ok:
        out["status"] = "BLOCKED" if res.status_code in (401, 403, 429) else "ERROR"
        _update_hit(hit_id, processing_status=out["status"],
                    http_status=res.status_code, note=res.error)
        return out

    verdict = classify_url_type(url, res.text, dom)
    out["hit_type"] = verdict["type"]
    country_info = detect_country(url, res.text)
    cat = domain_category(dom, res.text[:4000])
    listings = verdict.get("listings", []) or []
    had_listings = bool(listings)

    if verdict["type"] == "LISTING":
        source_id, monitorable = (None, False)
        if not aggregator and cat["category"] == MONITORABLE:
            source_id, monitorable = _register_source(
                dom, cat, country_info, "LISTING", None, country, query,
                providers, True)
        out["source_registered"] = source_id is not None
        out["monitorable"] = monitorable
        source = _load_source(source_id)
        best_id = None
        for raw in listings:
            raw.country = raw.country or country_info["country"]
            r = process_candidate(raw, source)   # DIRECT ingest from search!
            if r.listing is not None:
                best_id = r.listing.internal_id
                out["ingested"] = True
        out["status"] = "INGESTED" if out["ingested"] else "SKIPPED"
        _update_hit(hit_id, hit_type="LISTING", processing_status=out["status"],
                    http_status=res.status_code, listing_internal_id=best_id,
                    note=verdict["reason"])
        return out

    if verdict["type"] in ("DEALER_INVENTORY", "HOMEPAGE"):
        search_url = url if verdict["type"] == "DEALER_INVENTORY" else None
        source_id, monitorable = _register_source(
            dom, cat, country_info, verdict["type"], search_url, country, query,
            providers, had_listings)
        out["source_registered"] = source_id is not None
        out["monitorable"] = monitorable
        out["status"] = "SOURCE_REGISTERED"
        source = _load_source(source_id)
        for raw in listings[:20]:
            if "ignis" in (raw.combined_text() or "").lower():
                if process_candidate(raw, source).listing is not None:
                    out["ingested"] = True
        _update_hit(hit_id, hit_type=verdict["type"],
                    processing_status=out["status"], http_status=res.status_code,
                    note=verdict["reason"])
        return out

    # ARTICLE / PARTS_PAGE / FORUM_POST / IRRELEVANT → discovery-only, no crawl.
    if verdict["type"] == "FORUM_POST":
        for raw in listings:
            if "ignis" in (raw.combined_text() or "").lower():
                if process_candidate(raw, None).listing is not None:
                    out["ingested"] = True
    out["status"] = "SKIPPED"
    _update_hit(hit_id, hit_type=verdict["type"], processing_status="SKIPPED",
                http_status=res.status_code, note=verdict["reason"])
    return out
