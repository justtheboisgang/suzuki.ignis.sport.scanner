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
from ..ai.schemas import VisionVerdict
from ..ai.vehicle_detective import analyze_candidate
from ..ai.vision import (
    analyze_vehicle_images,
    merge_vision,
    should_run_vision,
    vision_cache_key,
)
from ..database.base import session_scope
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
from ..parsers.normalize import (
    detect_language,
    normalize_text,
    price_sanity,
    to_eur,
)
from ..scoring.opportunity import compute_opportunity
from ..utils.hashing import content_hash, normalize_url, stable_id
from ..utils.lhd_rhd import infer_lhd_rhd
from ..utils.logging import get_logger
from .expansion import expand_seller_to_source, finalize_links, set_provenance

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
    # Title-first: identity comes from the card's TITLE; the (card-isolated)
    # description only helps decide Sport vs. base once the title is an Ignis.
    pf = prefilter(raw.title or "",
                   f"{raw.description or ''} {raw.raw_text or ''}",
                   year=raw.year, power_kw=raw.power_kw, power_hp=raw.power_hp,
                   displacement_cc=raw.displacement_cc)
    if not pf.relevant:
        # OTHER_MODEL / IRRELEVANT — hard-rejected, never stored as a candidate.
        return IngestResult(listing=None, bucket=pf.bucket,
                            is_ignis=pf.is_ignis)

    baseline = score_confidence(pf, year=raw.year, power_kw=raw.power_kw,
                                power_hp=raw.power_hp,
                                displacement_cc=raw.displacement_cc)
    confidence = baseline.confidence
    classification = baseline.classification
    used_ai = False
    ai_verdict = None

    internal = stable_id(normalize_url(raw.url))

    # --- NETWORK/AI PHASE (no DB transaction open) -----------------------
    if pf.needs_ai:
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

    # Compute perceptual hashes when we'll need them for dedup OR for vision on
    # an ambiguous / bare-Ignis candidate.
    run_vision = should_run_vision(pf.bucket, bool(raw.images))
    image_hashes = (perceptual_hashes(raw.images)
                    if (is_sport_candidate or run_vision) and raw.images else [])

    # --- Claude Vision verification (ambiguous candidates only, cached) ---
    vision_verdict = None
    vision_reused = False
    if run_vision:
        cache_key = vision_cache_key(raw.images, image_hashes)
        prior = _load_existing_vision(internal)
        if prior and prior.get("vision_image_hash") == cache_key and prior.get("verdict"):
            vision_verdict = VisionVerdict(**prior["verdict"])
            vision_reused = True
        else:
            vision_verdict = analyze_vehicle_images(
                raw.images, image_hashes, target_id=internal)
        has_hard_negative = bool(pf.negative_signals) or \
            (raw.displacement_cc is not None and raw.displacement_cc < 1400) or \
            (raw.year is not None and raw.year >= 2015)
        merged = merge_vision(confidence, pf.bucket, pf.other_model,
                              has_hard_negative, vision_verdict)
        if not vision_verdict.is_fallback:
            confidence = merged["final_confidence"]
            if merged["classification"]:
                classification = merged["classification"]
            if merged["reasons"]:
                log.info("vision(%s) %s -> conf %d", internal[:8],
                         "cache" if vision_reused else "api", confidence)

    is_sport_candidate = confidence >= settings.match_confidence_threshold

    analysis = None
    if is_sport_candidate and confidence >= 70 and classification != "DATA_CONFLICT":
        analysis = analyze_listing(raw.title, raw.description or raw.raw_text or "",
                                   country=raw.country, seller_type=seller_type,
                                   target_id=internal)

    if pf.needs_ai and is_sport_candidate and classification not in (
            "DATA_CONFLICT",):
        classification = "MISLABELLED_SPORT_CANDIDATE"

    # Precompute plain column values so the write callable is retry-safe.
    desc = raw.description or raw.raw_text or ""
    price_eur_raw = to_eur(raw.price, raw.currency)
    # Guard against €1 placeholders / "price on request" / financing figures.
    price_eur, price_status = price_sanity(price_eur_raw, text,
                                           has_authoritative_offer=False)
    lhd, lhd_conf = infer_lhd_rhd(text, raw.country)
    src_is_dealer = bool(source and source.source_type in _DEALER_TYPES)

    # A candidate without a concrete individual detail URL is not shown as a
    # normal active listing — it is stored UNRESOLVED until we find the exact ad.
    from ..parsers.urltype import url_quality as _url_quality
    url_quality = getattr(raw, "listing_url_quality", "UNKNOWN")
    if url_quality == "UNKNOWN":
        url_quality = _url_quality(raw.url)
    has_detail = url_quality in ("EXACT_DETAIL", "LIKELY_DETAIL")
    status_val = (ListingStatus.ACTIVE.value if has_detail
                  else ListingStatus.UNRESOLVED.value)
    ai_analysis = None
    if ai_verdict is not None:
        ai_analysis = {"detective": ai_verdict.model_dump()}
    if analysis is not None:
        ai_analysis = {**(ai_analysis or {}), "analyst": analysis.model_dump()}

    # Vision columns (only when a non-fallback verdict was produced/reused).
    vision_kwargs = {}
    if run_vision and vision_verdict is not None and not vision_verdict.is_fallback:
        vision_kwargs = dict(
            vision_analyzed=True,
            vision_model=(get_settings().anthropic_model),
            vision_analyzed_at=datetime.now(timezone.utc),
            vision_image_hash=vision_cache_key(raw.images, image_hashes),
            vehicle_identity_confidence=vision_verdict.vehicle_identity_confidence,
            ignis_confidence=vision_verdict.ignis_confidence,
            ignis_sport_visual_confidence=vision_verdict.ignis_sport_visual_confidence,
            visible_positive_signals=vision_verdict.visible_positive_signals,
            visible_negative_signals=vision_verdict.visible_negative_signals,
            vision_uncertainties=vision_verdict.uncertainties,
            visual_summary=vision_verdict.visual_summary,
            vision_conflict=(classification == "DATA_CONFLICT"),
        )

    kwargs = dict(
        internal_id=internal, title=normalize_text(raw.title)[:500],
        make="Suzuki", model="Ignis",
        variant="Sport" if confidence >= 60 else None,
        production_year=raw.year, mileage_km=raw.mileage_km,
        displacement_cc=raw.displacement_cc, power_kw=raw.power_kw,
        power_hp=raw.power_hp, fuel=raw.fuel, transmission=raw.transmission,
        color=raw.color, price_original=raw.price, currency=raw.currency,
        price_eur=price_eur, price_parse_status=price_status,
        country=raw.country, seller_type=seller_type,
        seller_website=(source.base_url if src_is_dealer else None),
        listing_url=raw.url,
        original_listing_url=(raw.url if src_is_dealer else None),
        alternate_urls=[], source_id=(source.id if source else None),
        listing_status=status_val,
        listing_url_quality=url_quality,
        page_type=getattr(raw, "page_type", None),
        extraction_method=getattr(raw, "extraction_method", None),
        card_href_found=getattr(raw, "card_href_found", False),
        discovered_from_url=getattr(raw, "discovered_from_url", None),
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
        **vision_kwargs,
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
        # Recompute links now that dealer_domain/alternate URLs may be set.
        finalize_links(candidate)
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
    # A real seller price change is measured in the ORIGINAL currency/amount.
    # An EUR-only difference caused by FX drift is NOT a price drop (e.g.
    # 15000 PLN unchanged but €3450 → €3435).
    old_orig, new_orig = existing.price_original, candidate.price_original
    same_currency = (existing.currency or "") == (candidate.currency or "")
    real_change = (
        new_orig is not None and old_orig is not None and same_currency
        and abs(new_orig - old_orig) >= 1
    ) or (new_orig is not None and old_orig is not None and not same_currency)

    if real_change:
        session.add(PriceHistory(
            listing_id=existing.id, old_price_eur=existing.price_eur,
            new_price_eur=candidate.price_eur,
            delta_eur=(round((candidate.price_eur or 0) - (existing.price_eur or 0), 2)
                       if candidate.price_eur is not None and existing.price_eur is not None
                       else None)))
        price_changed = True
    # Always refresh stored price to the latest (for scoring), even on FX-only
    # moves — but that alone never creates a price-drop event above.
    if candidate.price_original is not None:
        existing.price_original = candidate.price_original
        existing.currency = candidate.currency
    if candidate.price_eur is not None:
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
    finalize_links(existing)  # keep canonical/original pointing at a real listing
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


def _load_existing_vision(internal_id: str) -> dict | None:
    """Return {vision_image_hash, verdict} for a stored listing, so unchanged
    images are never re-sent to Claude Vision (cost control)."""
    with session_scope() as s:
        row = s.query(Listing).filter(Listing.internal_id == internal_id).first()
        if not row or not row.vision_analyzed or not row.vision_image_hash:
            return None
        return {
            "vision_image_hash": row.vision_image_hash,
            "verdict": {
                "vehicle_identity_confidence": row.vehicle_identity_confidence or 0,
                "ignis_confidence": row.ignis_confidence or 0,
                "ignis_sport_visual_confidence": row.ignis_sport_visual_confidence or 0,
                "visible_positive_signals": row.visible_positive_signals or [],
                "visible_negative_signals": row.visible_negative_signals or [],
                "uncertainties": row.vision_uncertainties or [],
                "visual_summary": row.visual_summary or "",
                "is_fallback": False,
            },
        }
