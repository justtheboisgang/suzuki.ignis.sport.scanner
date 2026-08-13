"""One-shot re-evaluation of already-stored data with the current classifier.

Fixes historical contamination without deleting anything:
  * every Listing is re-run through the deterministic prefilter + confidence
    (so "Suzuki Wagon R" etc. drop to confidence 0 / NOT_IGNIS and disappear
    from active candidate lists), prices are re-sanity-checked, and opportunity
    is recomputed;
  * every Source is re-categorised (parts/tuning/insurer → DISCOVERY_ONLY,
    inactive), keeping its discovery provenance.

Pure CPU + DB (no network), run in short serialised write transactions.
"""

from __future__ import annotations

from ..classification.confidence import score_confidence
from ..classification.prefilter import prefilter
from ..database.base import session_scope
from ..database.writer import run_write
from ..discovery.classify import MONITORABLE, domain_category, is_aggregator_domain
from ..models.enums import Classification, ListingStatus
from ..models.listing import Listing
from ..models.source import Source
from ..parsers.normalize import price_sanity
from ..parsers.urltype import url_quality
from ..scoring.opportunity import compute_opportunity
from ..utils.logging import get_logger
from .expansion import finalize_links

log = get_logger("pipeline.reclassify")


def reclassify_listings(batch: int = 500) -> dict:
    counts = {"total": 0, "not_ignis": 0, "normal_ignis": 0, "possible": 0,
              "confirmed": 0, "price_fixed": 0, "unresolved_urls": 0}
    with session_scope() as s:
        ids = [i for (i,) in s.query(Listing.id).all()]

    for start in range(0, len(ids), batch):
        chunk = ids[start:start + batch]

        def _do(session, chunk=chunk):
            for lid in chunk:
                lst = session.get(Listing, lid)
                if not lst:
                    continue
                counts["total"] += 1
                # TITLE-FIRST: a contaminated historical description must never
                # re-upgrade an explicit other model (Bus/Jimny/Swift/…).
                pf = prefilter(lst.title or "", lst.description_original or "",
                               year=lst.production_year, power_kw=lst.power_kw,
                               power_hp=lst.power_hp,
                               displacement_cc=lst.displacement_cc)
                cr = score_confidence(pf, year=lst.production_year,
                                      power_kw=lst.power_kw, power_hp=lst.power_hp,
                                      displacement_cc=lst.displacement_cc)
                lst.vehicle_match_confidence = cr.confidence
                lst.classification = cr.classification
                lst.variant = "Sport" if cr.confidence >= 60 else None
                if cr.classification == Classification.NOT_IGNIS.value:
                    counts["not_ignis"] += 1
                elif cr.classification == Classification.NORMAL_IGNIS.value:
                    counts["normal_ignis"] += 1
                elif cr.confidence >= 80:
                    counts["confirmed"] += 1
                elif cr.confidence >= 60:
                    counts["possible"] += 1

                # Re-sanity the price using stored fields (year/original/mileage
                # aware, so year-as-price and part numbers are dropped).
                new_price, status = price_sanity(
                    lst.price_eur, lst.description_original or lst.title or "",
                    year=lst.production_year, price_original=lst.price_original,
                    mileage_km=lst.mileage_km)
                if status != "OK":
                    if lst.price_eur is not None and new_price is None:
                        counts["price_fixed"] += 1
                    lst.price_eur = new_price
                lst.price_parse_status = status

                # Recompute URL quality + best canonical link, and hide listings
                # whose only known URL is a search/inventory/homepage page.
                lst.listing_url_quality = url_quality(lst.listing_url)
                finalize_links(lst)
                if lst.external_detail_url is None:
                    counts["unresolved_urls"] += 1
                    if lst.listing_status in (ListingStatus.ACTIVE.value,
                                              ListingStatus.MAYBE_ACTIVE.value):
                        lst.listing_status = ListingStatus.UNRESOLVED.value
                elif lst.listing_status == ListingStatus.UNRESOLVED.value:
                    lst.listing_status = ListingStatus.ACTIVE.value

                # Recompute opportunity with the guards.
                analysis = (lst.ai_analysis or {}).get("analyst") if lst.ai_analysis else None
                opp = compute_opportunity(lst, analysis)
                lst.opportunity_score = opp.score
                lst.score_explanation = opp.as_dict()

        run_write(_do, label="reclassify.listings", swallow=True)

    log.info("Reclassified listings: %s", counts)
    return counts


def reclassify_sources() -> dict:
    counts = {"total": 0, "monitorable": 0, "discovery_only": 0, "deactivated": 0}
    with session_scope() as s:
        ids = [i for (i,) in s.query(Source.id).all()]

    def _do(session):
        for sid in ids:
            src = session.get(Source, sid)
            if not src:
                continue
            counts["total"] += 1
            # Seed marketplaces/classifieds are trusted monitorable sources.
            if src.discovery_method == "seed_list":
                if src.source_category != MONITORABLE:
                    src.source_category = MONITORABLE
                continue
            blob = f"{src.name or ''} {src.notes or ''}"
            cat = domain_category(src.domain, blob)
            src.source_category = cat["category"]
            src.source_relevance_confidence = cat["relevance"]
            monitorable = cat["monitorable"] and not is_aggregator_domain(src.domain)
            if monitorable:
                counts["monitorable"] += 1
            else:
                counts["discovery_only"] += 1
                if src.active:
                    counts["deactivated"] += 1
                src.active = False
                src.manual_review = True
                if src.discovery_value > 25:
                    src.discovery_value = 25

    run_write(_do, label="reclassify.sources", swallow=True)
    log.info("Reclassified sources: %s", counts)
    return counts


def reclassify_all() -> dict:
    return {"listings": reclassify_listings(), "sources": reclassify_sources()}
