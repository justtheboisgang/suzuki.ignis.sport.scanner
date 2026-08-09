"""Real-World Validation Report.

Composes the post-live-run picture entirely from persisted real data — scans,
discovery, provider provenance, vehicles and long-tail wins. It NEVER fabricates
listings; if nothing was found it says so and shows what was actually checked.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from ..database.base import session_scope
from ..models.enums import ListingStatus, SourceLiveStatus
from ..models.listing import Listing
from ..models.provider import DomainDiscovery, ProviderUsage
from ..models.scan import ScanRun
from ..models.search_hit import SearchHit
from ..models.source import Source


def provider_comparison() -> dict:
    """Brave-unique vs SerpApi-unique vs overlap domains."""
    with session_scope() as s:
        pairs = s.query(DomainDiscovery.domain, DomainDiscovery.provider).distinct().all()
    by_domain: dict[str, set] = {}
    for dom, prov in pairs:
        by_domain.setdefault(dom, set()).add(prov)
    brave_only = serp_only = overlap = 0
    for provs in by_domain.values():
        has_b, has_s = "brave" in provs, "serpapi" in provs
        if has_b and has_s:
            overlap += 1
        elif has_b:
            brave_only += 1
        elif has_s:
            serp_only += 1
    return {"brave_unique_domains": brave_only, "serpapi_unique_domains": serp_only,
            "overlap_domains": overlap, "total_domains_seen": len(by_domain)}


def build_report() -> dict:
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        scans = s.query(ScanRun).filter(ScanRun.kind == "scan").all()
        pages = sum(r.pages_checked or 0 for r in scans)
        queries_run = s.query(ProviderUsage).count()

        def live(status):
            return s.query(Source).filter(Source.live_status == status).count()

        sources_total = s.query(Source).count()
        working = live(SourceLiveStatus.WORKING.value)
        blocked = (live(SourceLiveStatus.BLOCKED.value)
                   + live(SourceLiveStatus.ROBOTS_DISALLOWED.value)
                   + live(SourceLiveStatus.LOGIN_REQUIRED.value))
        failed = (live(SourceLiveStatus.DEAD.value)
                  + live(SourceLiveStatus.PARSER_BROKEN.value))

        # Source-quality split (P4).
        monitorable = s.query(Source).filter(
            Source.source_category == "MONITORABLE_VEHICLE_SOURCE").count()
        discovery_only = s.query(Source).filter(
            Source.source_category == "DISCOVERY_ONLY_SOURCE").count()
        active_sources = s.query(Source).filter(Source.active.is_(True)).count()

        # Search-hit examination summary (P2/P3/P8).
        hit_total = s.query(SearchHit).count()

        def hit_count(**flt):
            q = s.query(SearchHit)
            for k, v in flt.items():
                q = q.filter(getattr(SearchHit, k) == v)
            return q.count()

        hits_by_status = {st: hit_count(processing_status=st) for st in
                          ("INGESTED", "SOURCE_REGISTERED", "SKIPPED", "BLOCKED",
                           "ERROR", "PENDING")}
        recent_hits = (s.query(SearchHit)
                       .order_by(SearchHit.discovered_at.desc()).limit(25).all())
        examined_rows = [{"url": h.exact_url, "type": h.hit_type,
                          "status": h.processing_status, "http": h.http_status,
                          "query": h.query, "note": (h.note or "")[:60]}
                         for h in recent_hits]

        new_dealers = s.query(Source).filter(Source.seller_derived.is_(True)).count()
        search_dealers = s.query(Source).filter(
            Source.discovery_method.like("search:%"),
            Source.source_type.in_(["dealer", "suzuki_dealer", "garage",
                                    "youngtimer_dealer"])).count()
        new_marketplaces = s.query(Source).filter(
            Source.discovery_method.like("search:%"),
            Source.source_type.in_(["major_marketplace", "classifieds"])).count()
        new_forums = s.query(Source).filter(
            Source.discovery_method.like("search:%"),
            Source.source_type.in_(["forum", "club", "community"])).count()

        def conf_count(lo, hi):
            return s.query(Listing).filter(
                Listing.vehicle_match_confidence >= lo,
                Listing.vehicle_match_confidence < hi).count()

        confirmed = s.query(Listing).filter(Listing.vehicle_match_confidence >= 95).count()
        likely = conf_count(80, 95)
        mislabelled = s.query(Listing).filter(
            Listing.classification == "MISLABELLED_SPORT_CANDIDATE").count()
        normal_ignis = s.query(Listing).filter(
            Listing.vehicle_match_confidence < 40).count()
        archived = s.query(Listing).filter(
            Listing.listing_status.in_([ListingStatus.EXPIRED.value,
                                        ListingStatus.ARCHIVED.value,
                                        ListingStatus.REMOVED.value])).count()

        best = (s.query(Listing)
                .filter(Listing.vehicle_match_confidence >= 60,
                        Listing.listing_status.in_([ListingStatus.ACTIVE.value,
                                                    ListingStatus.MAYBE_ACTIVE.value]))
                .order_by(Listing.opportunity_score.desc().nullslast(),
                          Listing.vehicle_match_confidence.desc())
                .limit(25).all())
        best_rows = [{
            "title": l.title, "price_eur": l.price_eur, "mileage_km": l.mileage_km,
            "year": l.production_year, "country": l.country,
            "seller": l.seller_name, "match": l.vehicle_match_confidence,
            "opportunity": l.opportunity_score,
            "first_seen": l.first_seen_at.isoformat() if l.first_seen_at else None,
            "canonical_url": l.canonical_listing_url or l.listing_url,
            "long_tail": l.is_long_tail,
        } for l in best]

        long_tail_wins = [r for r in best_rows if r["long_tail"]]

        countries_attempted = s.query(Source.country).filter(
            Source.last_checked_at.isnot(None)).distinct().count()
        sources_attempted = s.query(Source).filter(
            Source.last_checked_at.isnot(None)).count()

        failures = s.query(Source).filter(
            Source.live_status.in_([SourceLiveStatus.BLOCKED.value,
                                    SourceLiveStatus.DEAD.value,
                                    SourceLiveStatus.PARSER_BROKEN.value,
                                    SourceLiveStatus.LOGIN_REQUIRED.value,
                                    SourceLiveStatus.ROBOTS_DISALLOWED.value])
        ).limit(50).all()
        failure_rows = [{"domain": f.domain, "reason": f.live_status,
                         "detail": (f.live_status_detail or "")[:80]} for f in failures]

    return {
        "generated_at": now.isoformat(),
        "scan": {
            "countries_attempted": countries_attempted,
            "sources_attempted": sources_attempted,
            "sources_working": working,
            "sources_blocked": blocked,
            "sources_failed": failed,
            "pages_fetched": pages,
            "search_queries_executed": queries_run,
        },
        "discovery": {
            "known_domains_now": sources_total,
            "monitorable_vehicle_sources": monitorable,
            "discovery_only_sources": discovery_only,
            "active_sources": active_sources,
            "new_dealer_domains_from_sellers": new_dealers,
            "new_dealer_domains_from_search": search_dealers,
            "new_marketplaces": new_marketplaces,
            "new_forums_clubs": new_forums,
        },
        "search_hits": {
            "total": hit_total,
            "by_status": hits_by_status,
            "recent_examined": examined_rows,
        },
        "vehicles": {
            "confirmed_ignis_sports": confirmed,
            "likely_ignis_sports": likely,
            "mislabelled_sport_candidates": mislabelled,
            "normal_ignis": normal_ignis,
            "archived_listings": archived,
        },
        "best_current_listings": best_rows,
        "long_tail_wins": long_tail_wins,
        "provider_comparison": provider_comparison(),
        "source_failures": failure_rows,
    }


def print_report(rep: dict) -> None:
    def hdr(t):
        print("\n" + "=" * 56 + f"\n{t}\n" + "=" * 56)

    print("################ REAL WORLD VALIDATION REPORT ################")
    print("generated:", rep["generated_at"])

    hdr("SCAN")
    for k, v in rep["scan"].items():
        print(f"  {k.replace('_',' ').title():<28} {v}")

    hdr("DISCOVERY")
    for k, v in rep["discovery"].items():
        print(f"  {k.replace('_',' ').title():<34} {v}")

    hdr("VEHICLES")
    for k, v in rep["vehicles"].items():
        print(f"  {k.replace('_',' ').title():<30} {v}")

    hdr("BEST CURRENT LISTINGS")
    if not rep["best_current_listings"]:
        print("  (none found yet — see SCAN/source failures for why)")
    for r in rep["best_current_listings"]:
        price = f"€{r['price_eur']:,.0f}" if r["price_eur"] else "?"
        print(f"  [{r['match']:>3}|opp {r['opportunity']}] {r['country'] or '?'} "
              f"{(r['title'] or '')[:48]:<48} {price} {r['mileage_km'] or '?'}km "
              f"{r['year'] or '?'}  {r['canonical_url'][:50]}")

    hdr("LONG-TAIL WINS (not from the obvious big platforms)")
    if not rep["long_tail_wins"]:
        print("  (none yet)")
    for r in rep["long_tail_wins"]:
        print(f"  {r['country'] or '?'} {(r['title'] or '')[:50]} -> {r['canonical_url'][:60]}")

    hdr("SEARCH HITS EXAMINED (concrete results investigated / discarded)")
    sh = rep["search_hits"]
    print(f"  total stored: {sh['total']} | by status: {sh['by_status']}")
    for h in sh["recent_examined"]:
        print(f"  [{h['type']:<16} {h['status']:<16} {h['http'] or '-'}] "
              f"{(h['url'] or '')[:70]}")
        if h["note"]:
            print(f"        ↳ {h['note']}")

    hdr("PROVIDER COMPARISON")
    for k, v in rep["provider_comparison"].items():
        print(f"  {k.replace('_',' ').title():<28} {v}")

    hdr("SOURCE FAILURES")
    if not rep["source_failures"]:
        print("  (none recorded)")
    for f in rep["source_failures"]:
        print(f"  {f['domain']:<28} {f['reason']:<18} {f['detail']}")
