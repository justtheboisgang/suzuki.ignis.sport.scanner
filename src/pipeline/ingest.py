"""Turn a single RawListing candidate into a persisted, de-duplicated,
classified and scored Listing — recording price/status history along the way.

Concurrency contract (fixes the production DB-lock bug): ALL network/AI work
(vehicle detective, image hashing, listing analyst) happens FIRST, with no DB
transaction open. Only then do we persist inside ONE short, serialised write
transaction (`run_write`). The listing object is constructed *inside* that
callable so a transient-lock retry re-runs cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ai.listing_analyst import analyze_listing
from ..ai.vehicle_detective import analyze_candidate
from ..classification.confidence import score_confidence
from ..classification.prefilter import prefilter
from ..config.settings import get_settings
from ..database.writer import run_write
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
from .expansion import expand_seller_to_source, set_provenance

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
    return SellerType.UNKNOWN.value


def process_candidate(raw: RawListing, source: Source | None) -> IngestResult:
    """Classify + persist one candidate. `source` may be a detached ORM object
    (only its plain attributes are read); all writes go through a fresh session.
    Networks/AI run before the write; the write itself is short and serialised.
    """
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

    internal = stable_id(normalize_url(raw.url))

    # --- NETWORK/AI PHASE (no DB transaction open) -----------------------
    if pf.bucket == "NEEDS_AI":
        ai_verdict = analyze_candidate(
            title=raw.title, description=raw.description or raw.raw_text,
            pf=pf, baseline=baseline, year=raw.year, power_kw=raw.power_kw,
            power_hp=raw.power_hp, displacement_cc=raw.displacement_cc,
            target_id=internal)
        used_ai = not ai_verdict.is_fallback
        confidence = ai_verdict.vehicle_match_confidence
        classification = ai_verdict.classification

    is_sport_candidate = confidence >= settings.match_confidence_threshold
    seller_type = _seller_type(raw, source)

    image_hashes = (perceptual_hashes(raw.images)
                    if is_sport_candidate and raw.images else [])

    analysis = None
    if is_sport_candidate and confidence >= 70:
        analysis = analyze_listing(raw.title, raw.description or raw.raw_text or "",
                                   country=raw.country, seller_type=seller_type,
                                   target_id=internal)

    if pf.bucket == "NEEDS_AI" and is_sport_candidate:
        classification = "MISLABELLED_SPORT_CANDIDATE"

    # Precompute plain column values so the write callable is retry-safe.
    desc = raw.description or raw.raw_text or ""
    price_eur = to_eur(raw.price, raw.currency)
    lhd, lhd_conf = infer_lhd_rhd(text, raw.country)
    src_is_dealer = bool(source and source.source_type in _DEALER_TYPES)
    ai_analysis = None
    if ai_verdict is not None:
        ai_analysis = {"detective": ai_verdict.model_dump()}
    if analysis is not None:
        ai_analysis = {**(ai_analysis or {}), "analyst": analysis.model_dump()}

    kwargs = dict(
        internal_id=internal, title=normalize_text(raw.title)[:500],
        make="Suzuki", model="Ignis",
        variant="Sport" if confidence >= 60 else None,
        production_year=raw.year, mileage_km=raw.mileage_km,
        displacement_cc=raw.displacement_cc, power_kw=raw.power_kw,
        power_hp=raw.power_hp, fuel=raw.fuel, transmission=raw.transmission,
        color=raw.color, price_original=raw.price, currency=raw.currency,
        price_eur=price_eur, country=raw.country, seller_type=seller_type,
        seller_website=(source.base_url if src_is_dealer else None),
        listing_url=raw.url,
        original_listing_url=(raw.url if src_is_dealer else None),
        alternate_urls=[], source_id=(source.id if source else None),
        listing_status=ListingStatus.ACTIVE.value,
        description_original=desc or None,
        description_language=detect_language(desc),
        description_normalized=normalize_text(desc)[:5000] or None,
        content_hash=content_hash(raw.title, desc, str(raw.price),
                                  str(raw.mileage_km)),
        vin_public=raw.vin,
        chassis_code=("HT81S" if "ht81s" in text.lower() else None),
        lhd_rhd=lhd, lhd_rhd_confidence=lhd_conf,
        image_urls=raw.images or [], image_count=len(raw.images or []),
        image_hashes=image_hashes, vehicle_match_confidence=confidence,
        source_confidence=(source.discovery_value if source else 50),
        classification=classification, ai_analysis=ai_analysis,
    )

    source_id = source.id if source else None
    outcome: dict = {}

    # --- SHORT WRITE PHASE (serialised, retry-safe) ----------------------
    def _persist(session):
        candidate = Listing(**kwargs)
        existing = find_duplicate(session, candidate)
        if existing is not None:
            price_changed = _update_existing(session, existing, candidate, source)
            outcome.update(listing=existing, new=False, merged=True,
                           price_changed=price_changed)
            return
        session.add(candidate)
        session.flush()
        _record_status(session, candidate, None, candidate.listing_status,
                       "first seen")
        set_provenance(candidate, source)
        if is_sport_candidate:
            try:
                expand_seller_to_source(session, candidate, source)
            except Exception as exc:  # expansion must never break ingestion
                log.debug("seller expansion failed: %s", exc)
        if analysis is not None:
            _apply_analysis(candidate, analysis)
        _rescore(candidate)
        if source_id is not None:
            src = session.get(Source, source_id)
            if src:
                src.vehicles_found_total += 1
                src.ignis_found_total += 1
                if is_sport_candidate:
                    src.ignis_sport_found_total += 1
        outcome.update(listing=candidate, new=True, merged=False,
                       price_changed=False)

    run_write(_persist, label="ingest.persist")

    return IngestResult(
        listing=outcome.get("listing"), is_new=outcome.get("new", False),
        is_ignis=True, is_sport_candidate=is_sport_candidate, used_ai=used_ai,
        duplicate_merged=outcome.get("merged", False),
        price_changed=outcome.get("price_changed", False), bucket=pf.bucket)


def _update_existing(session, existing: Listing, candidate: Listing,
                     source: Source | None) -> bool:
    now = datetime.now(timezone.utc)
    price_changed = False
    if (candidate.price_eur is not None and existing.price_eur is not None
            and abs(candidate.price_eur - existing.price_eur) >= 1):
        session.add(PriceHistory(
            listing_id=existing.id, old_price_eur=existing.price_eur,
            new_price_eur=candidate.price_eur,
            delta_eur=round(candidate.price_eur - existing.price_eur, 2)))
        existing.price_original = candidate.price_original
        existing.currency = candidate.currency
        existing.price_eur = candidate.price_eur
        price_changed = True

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
    return price_changed


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
