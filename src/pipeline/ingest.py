"""Turn a single RawListing candidate into a persisted, de-duplicated,
classified and scored Listing — recording price/status history along the way.

This is where the deterministic pre-filter decides who (rarely) gets the AI
treatment, so Claude only ever sees genuinely ambiguous cars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..ai.listing_analyst import analyze_listing
from ..ai.vehicle_detective import analyze_candidate
from ..classification.confidence import score_confidence
from ..classification.prefilter import prefilter
from ..config.settings import get_settings
from ..deduplication.dedup import find_duplicate, merge_into
from ..deduplication.imagehashing import perceptual_hashes
from ..models.enums import ListingStatus, SellerType
from ..models.history import PriceHistory, StatusHistory
from ..models.listing import Listing
from ..models.source import Source
from ..parsers.html_generic import RawListing
from ..parsers.normalize import detect_language, normalize_text, to_eur
from ..scoring.opportunity import compute_opportunity
from ..utils.hashing import content_hash, normalize_url, stable_id
from ..utils.lhd_rhd import infer_lhd_rhd
from ..utils.logging import get_logger

log = get_logger("pipeline.ingest")

_DEALER_TYPES = {"dealer", "suzuki_dealer", "garage", "youngtimer_dealer",
                 "enthusiast_dealer", "private_site"}


@dataclass
class IngestResult:
    listing: Listing | None
    is_new: bool = False
    is_ignis: bool = False
    is_sport_candidate: bool = False
    used_ai: bool = False
    price_changed: bool = False
    duplicate_merged: bool = False
    bucket: str = "IRRELEVANT"


def _seller_type(raw: RawListing, source: Source | None) -> str:
    if source and source.source_type in _DEALER_TYPES:
        return SellerType.DEALER.value
    if source and source.source_type in {"classifieds", "local_marketplace"}:
        return SellerType.UNKNOWN.value
    return SellerType.UNKNOWN.value


def process_candidate(session: Session, raw: RawListing,
                      source: Source | None) -> IngestResult:
    settings = get_settings()
    text = raw.combined_text()

    pf = prefilter(text, year=raw.year, power_kw=raw.power_kw,
                   power_hp=raw.power_hp, displacement_cc=raw.displacement_cc)

    if not pf.relevant:
        return IngestResult(listing=None, bucket=pf.bucket, is_ignis=False)

    baseline = score_confidence(pf, year=raw.year, power_kw=raw.power_kw,
                                power_hp=raw.power_hp,
                                displacement_cc=raw.displacement_cc)
    confidence = baseline.confidence
    classification = baseline.classification
    used_ai = False
    ai_verdict = None

    # Only the genuinely ambiguous bucket goes to Claude.
    if pf.bucket == "NEEDS_AI":
        ai_verdict = analyze_candidate(
            title=raw.title, description=raw.description or raw.raw_text,
            pf=pf, baseline=baseline, year=raw.year, power_kw=raw.power_kw,
            power_hp=raw.power_hp, displacement_cc=raw.displacement_cc,
            target_id=stable_id(raw.url),
        )
        used_ai = not ai_verdict.is_fallback
        confidence = ai_verdict.vehicle_match_confidence
        classification = ai_verdict.classification

    # Build the canonical listing record.
    price_eur = to_eur(raw.price, raw.currency)
    desc = raw.description or raw.raw_text or ""
    chash = content_hash(raw.title, desc, str(raw.price), str(raw.mileage_km))
    lhd, lhd_conf = infer_lhd_rhd(text, raw.country)
    internal = stable_id(normalize_url(raw.url))

    candidate = Listing(
        internal_id=internal,
        title=normalize_text(raw.title)[:500],
        make="Suzuki",
        model="Ignis",
        variant="Sport" if confidence >= 60 else None,
        production_year=raw.year,
        mileage_km=raw.mileage_km,
        engine=raw.combined_text()[:0] or None,
        displacement_cc=raw.displacement_cc,
        power_kw=raw.power_kw,
        power_hp=raw.power_hp,
        fuel=raw.fuel,
        transmission=raw.transmission,
        color=raw.color,
        price_original=raw.price,
        currency=raw.currency,
        price_eur=price_eur,
        country=raw.country,
        seller_type=_seller_type(raw, source),
        seller_website=(source.base_url if source and source.source_type in _DEALER_TYPES else None),
        listing_url=raw.url,
        original_listing_url=(raw.url if source and source.source_type in _DEALER_TYPES else None),
        alternate_urls=[],
        source_id=source.id if source else None,
        listing_status=ListingStatus.ACTIVE.value,
        description_original=desc or None,
        description_language=detect_language(desc),
        description_normalized=normalize_text(desc)[:5000] or None,
        content_hash=chash,
        vin_public=raw.vin,
        chassis_code=("HT81S" if "ht81s" in text.lower() else None),
        lhd_rhd=lhd,
        lhd_rhd_confidence=lhd_conf,
        image_urls=raw.images or [],
        image_count=len(raw.images or []),
        image_hashes=[],
        vehicle_match_confidence=confidence,
        source_confidence=source.discovery_value if source else 50,
        classification=classification,
    )

    if ai_verdict is not None:
        candidate.ai_analysis = {
            "detective": ai_verdict.model_dump(),
        }

    # Compute image hashes only for real candidates (cost control).
    is_sport_candidate = confidence >= settings.match_confidence_threshold
    if is_sport_candidate and raw.images:
        candidate.image_hashes = perceptual_hashes(raw.images)

    # --- De-duplication against existing listings. -----------------------
    existing = find_duplicate(session, candidate)
    if existing is not None:
        _update_existing(session, existing, candidate, raw, source)
        return IngestResult(listing=existing, is_new=False, is_ignis=True,
                            is_sport_candidate=is_sport_candidate, used_ai=used_ai,
                            duplicate_merged=True, bucket=pf.bucket)

    # Mark a mislabelled-Sport candidate (bare Ignis routed to AI with Sport
    # signals) for the report's dedicated bucket.
    if pf.bucket == "NEEDS_AI" and confidence >= settings.match_confidence_threshold:
        candidate.classification = "MISLABELLED_SPORT_CANDIDATE"

    # --- New listing. ----------------------------------------------------
    session.add(candidate)
    session.flush()
    _record_status(session, candidate, None, candidate.listing_status, "first seen")

    # Provenance chain + seller→source expansion (reverse discovery).
    from .expansion import expand_seller_to_source, set_provenance
    set_provenance(candidate, source)
    if is_sport_candidate:
        try:
            expand_seller_to_source(session, candidate, source)
        except Exception as exc:  # expansion must never break ingestion
            log.debug("seller expansion failed: %s", exc)

    # Deeper AI analysis (condition/risks) for strong, fresh hits only.
    if is_sport_candidate and confidence >= 70:
        analysis = analyze_listing(raw.title, desc, country=raw.country,
                                   seller_type=candidate.seller_type,
                                   target_id=candidate.internal_id)
        candidate.ai_analysis = {**(candidate.ai_analysis or {}),
                                 "analyst": analysis.model_dump()}
        _apply_analysis(candidate, analysis)

    _rescore(candidate)
    _bump_source_counts(source, is_ignis=True,
                        is_sport=confidence >= settings.match_confidence_threshold)

    return IngestResult(listing=candidate, is_new=True, is_ignis=True,
                        is_sport_candidate=is_sport_candidate, used_ai=used_ai,
                        bucket=pf.bucket)


def _update_existing(session, existing: Listing, candidate: Listing,
                     raw: RawListing, source: Source | None):
    now = datetime.now(timezone.utc)
    # Price change tracking.
    if (candidate.price_eur is not None and existing.price_eur is not None
            and abs(candidate.price_eur - existing.price_eur) >= 1):
        session.add(PriceHistory(
            listing_id=existing.id, old_price_eur=existing.price_eur,
            new_price_eur=candidate.price_eur,
            delta_eur=round(candidate.price_eur - existing.price_eur, 2)))
        existing.price_original = candidate.price_original
        existing.currency = candidate.currency
        existing.price_eur = candidate.price_eur

    if existing.listing_status != ListingStatus.ACTIVE.value:
        _record_status(session, existing, existing.listing_status,
                       ListingStatus.ACTIVE.value, "re-listed / re-seen")
        existing.listing_status = ListingStatus.ACTIVE.value

    existing.consecutive_misses = 0
    existing.last_seen_at = now
    existing.last_verified_at = now
    dealer = bool(source and source.source_type in _DEALER_TYPES)
    merge_into(existing, candidate, candidate_is_dealer=dealer)
    _rescore(existing)


def _apply_analysis(listing: Listing, analysis) -> None:
    notes = []
    if analysis.rust_mentioned:
        notes.append("rust mentioned")
    if analysis.accident_mentioned:
        notes.append("accident mentioned")
    if analysis.engine_issues:
        notes.append("engine issues")
    if analysis.transmission_issues:
        notes.append("transmission issues")
    listing.damage_notes = "; ".join(notes) or None
    if analysis.service_history_present:
        listing.service_history = "present"
    if analysis.inspection_valid:
        listing.inspection_info = analysis.inspection_valid


def _rescore(listing: Listing) -> None:
    analysis = None
    if listing.ai_analysis and "analyst" in listing.ai_analysis:
        analysis = listing.ai_analysis["analyst"]
    result = compute_opportunity(listing, analysis)
    listing.opportunity_score = result.score
    listing.score_explanation = result.as_dict()


def _record_status(session, listing, old, new, note):
    session.add(StatusHistory(listing_id=listing.id, old_status=old,
                              new_status=new, note=note))


def _bump_source_counts(source: Source | None, is_ignis: bool, is_sport: bool):
    if not source:
        return
    source.vehicles_found_total += 1
    if is_ignis:
        source.ignis_found_total += 1
    if is_sport:
        source.ignis_sport_found_total += 1
