"""Read-side query helpers for the dashboard. Kept separate from routing so
they're independently testable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config.countries import COUNTRIES
from ..models.discovery import DiscoveryQuery
from ..models.enums import Classification, ListingStatus
from ..models.feedback import Notification
from ..models.listing import Listing
from ..models.scan import ScanRun
from ..models.source import Source

ACTIVE = (ListingStatus.ACTIVE.value, ListingStatus.MAYBE_ACTIVE.value)
_BAD_URL_QUALITY = ["SEARCH_PAGE", "HOMEPAGE"]


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def candidate_conditions(include_status: bool = True) -> list:
    """THE single definition of a 'valid candidate' shown on the dashboard,
    reused by every table AND every counter so they can never disagree:
      * status ACTIVE / MAYBE_ACTIVE (never UNRESOLVED),
      * classification is not NOT_IGNIS (drops other-model records),
      * URL is not a search/inventory/homepage-only page.
    """
    conds = [
        or_(Listing.classification.is_(None),
            Listing.classification != Classification.NOT_IGNIS.value),
        or_(Listing.listing_url_quality.is_(None),
            ~Listing.listing_url_quality.in_(_BAD_URL_QUALITY)),
    ]
    if include_status:
        conds.append(Listing.listing_status.in_(ACTIVE))
    return conds


def candidate_query(session: Session):
    q = session.query(Listing)
    for c in candidate_conditions():
        q = q.filter(c)
    return q


def filter_listings(session: Session, *, min_confidence=0, max_confidence=None,
                    country=None, max_price=None, min_price=None, max_mileage=None,
                    min_year=None, seller_type=None, status=None, lhd=None,
                    order_by="opportunity", limit=200, only_resolved=True
                    ) -> list[Listing]:
    q = session.query(Listing).filter(Listing.vehicle_match_confidence >= min_confidence)
    if only_resolved:
        # Apply the single shared valid-candidate predicate. If the caller asked
        # for a specific status, honour it instead of the ACTIVE/MAYBE default.
        for c in candidate_conditions(include_status=not status):
            q = q.filter(c)
    if max_confidence is not None:
        q = q.filter(Listing.vehicle_match_confidence <= max_confidence)
    if country:
        q = q.filter(Listing.country == country.upper())
    if max_price:
        q = q.filter(Listing.price_eur <= max_price)
    if min_price:
        q = q.filter(Listing.price_eur >= min_price)
    if max_mileage:
        q = q.filter(Listing.mileage_km <= max_mileage)
    if min_year:
        q = q.filter(Listing.production_year >= min_year)
    if seller_type:
        q = q.filter(Listing.seller_type == seller_type)
    if lhd:
        q = q.filter(Listing.lhd_rhd == lhd)
    if status:
        q = q.filter(Listing.listing_status == status)
    if order_by == "opportunity":
        q = q.order_by(Listing.opportunity_score.desc().nullslast(),
                       Listing.vehicle_match_confidence.desc())
    elif order_by == "newest":
        q = q.order_by(Listing.first_seen_at.desc())
    elif order_by == "confidence":
        q = q.order_by(Listing.vehicle_match_confidence.desc())
    elif order_by == "price":
        q = q.order_by(Listing.price_eur.asc().nullslast())
    return q.limit(limit).all()


def active_hits(session, min_confidence=60, limit=200):
    return filter_listings(session, min_confidence=min_confidence,
                           status=None, limit=limit)


def home_stats(session) -> dict:
    now = datetime.now(timezone.utc)
    today = now - timedelta(hours=24)

    # Counters use the SAME valid-candidate predicate as the tables, so the
    # headline numbers can never include historical NOT_IGNIS / UNRESOLVED rows.
    def active_q():
        return candidate_query(session)

    threshold = 60
    new_today = active_q().filter(Listing.vehicle_match_confidence >= threshold,
                                  Listing.first_seen_at >= today).count()
    total_active = active_q().filter(
        Listing.vehicle_match_confidence >= threshold).count()
    uncertain = active_q().filter(
        Listing.vehicle_match_confidence >= 40,
        Listing.vehicle_match_confidence < threshold).count()

    last_scan = session.query(ScanRun).filter(ScanRun.kind == "scan")\
        .order_by(ScanRun.start_time.desc()).first()

    disappeared = session.query(Listing).filter(
        Listing.listing_status.in_([ListingStatus.EXPIRED.value,
                                    ListingStatus.REMOVED.value])).count()
    failed_sources = session.query(Source).filter(Source.health == "failed").count()
    degraded_sources = session.query(Source).filter(Source.health == "degraded").count()

    return {
        "new_today": new_today,
        "total_active": total_active,
        "uncertain": uncertain,
        "disappeared": disappeared,
        "failed_sources": failed_sources,
        "degraded_sources": degraded_sources,
        "last_scan": last_scan,
        "sources_total": session.query(Source).count(),
        "sources_active": session.query(Source).filter(Source.active.is_(True)).count(),
    }


def secondary_metrics(session) -> dict:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_sources_week = session.query(Source).filter(
        Source.first_discovered_at >= week_ago).count()
    long_tail = session.query(Source).filter(Source.discovery_value >= 65).count()
    countries = session.query(Listing.country).distinct().count()
    dealer_first = session.query(Listing).filter(
        Listing.original_listing_url.isnot(None)).count()
    confirmed = session.query(Listing).filter(
        Listing.vehicle_match_confidence >= 95).count()
    return {
        "known_sources": session.query(Source).count(),
        "active_sources": session.query(Source).filter(Source.active.is_(True)).count(),
        "countries_covered": countries,
        "long_tail_sources": long_tail,
        "new_sources_week": new_sources_week,
        "ignis_seen": session.query(Listing).count(),
        "confirmed_sports": confirmed,
        "listings_first_on_dealer": dealer_first,
    }


def country_coverage(session) -> list[dict]:
    rows = []
    counts = dict(session.query(Source.country, func.count(Source.id))
                  .group_by(Source.country).all())
    type_counts = session.query(Source.country, Source.source_type,
                                func.count(Source.id))\
        .group_by(Source.country, Source.source_type).all()
    healthy = dict(session.query(Source.country, func.count(Source.id))
                   .filter(Source.health == "healthy")
                   .group_by(Source.country).all())
    failed = dict(session.query(Source.country, func.count(Source.id))
                  .filter(Source.health == "failed")
                  .group_by(Source.country).all())
    by_type: dict[str, dict[str, int]] = {}
    for country, stype, n in type_counts:
        by_type.setdefault(country, {})[stype] = n

    for code, country in COUNTRIES.items():
        rows.append({
            "code": code,
            "name": country.name_en,
            "sources": counts.get(code, 0),
            "types": by_type.get(code, {}),
            "healthy": healthy.get(code, 0),
            "failed": failed.get(code, 0),
        })
    rows.sort(key=lambda r: r["sources"], reverse=True)
    return rows


def source_health(session) -> dict:
    return {
        "healthy": session.query(Source).filter(Source.health == "healthy").count(),
        "degraded": session.query(Source).filter(Source.health == "degraded").count(),
        "failed": session.query(Source).filter(Source.health == "failed").count(),
        "unknown": session.query(Source).filter(Source.health == "unknown").count(),
        "manual_review": session.query(Source).filter(
            Source.manual_review.is_(True)).count(),
    }


def recent_notifications(session, limit=30):
    return session.query(Notification).order_by(
        Notification.created_at.desc()).limit(limit).all()


def newest_sources(session, limit=25):
    return session.query(Source).order_by(
        Source.first_discovered_at.desc()).limit(limit).all()


def price_drops(session, limit=25):
    from ..models.history import PriceHistory
    return session.query(PriceHistory).filter(PriceHistory.delta_eur < 0)\
        .order_by(PriceHistory.changed_at.desc()).limit(limit).all()


def discovery_effectiveness(session, limit=20):
    return session.query(DiscoveryQuery).order_by(
        DiscoveryQuery.effectiveness_score.desc()).limit(limit).all()
