"""Claude Vision verification for ambiguous Ignis candidates.

Runs ONLY when text/technical signals are inconclusive (POSSIBLE_IGNIS_SPORT,
UNKNOWN, or a bare "Suzuki Ignis" that might be a mislabelled Sport) and images
are available — never on explicitly other Suzuki models (those are rejected
deterministically before any AI). Two stages: (A) is it an Ignis? (B) does it
show Ignis-Sport cues? Results are cached by image-hash so unchanged listings
are never re-billed.
"""

from __future__ import annotations

import base64

import httpx

from ..config.settings import get_settings
from ..config.target import TARGET
from ..utils.hashing import content_hash
from ..utils.logging import get_logger
from .client import get_client
from .schemas import VisionVerdict

log = get_logger("ai.vision")

# Buckets (from the deterministic prefilter) that may benefit from vision.
_VISION_BUCKETS = {"POSSIBLE_IGNIS_SPORT", "UNKNOWN", "NORMAL_IGNIS"}

_SYSTEM = f"""You visually verify used-car photos for a hunter looking for the
first-generation Suzuki Ignis Sport (chassis {TARGET.chassis_code}).

Work in two stages and be strict — never invent details:
  STAGE A: Is the depicted car a Suzuki Ignis of the ~2003-2008 generation
           (a small, tall 3/5-door hatch)? Give `ignis_confidence` 0-100.
  STAGE B: If it plausibly is an Ignis, does it show SPORT (HT81S) cues?
           Look for: deeper sport front/rear bumpers, side skirts, roof
           spoiler, 15" sport alloy wheels, sport seats, 'Sport' badging, body
           kit. Give `ignis_sport_visual_confidence` 0-100.

List concrete `visible_positive_signals` and `visible_negative_signals` you can
actually see, and put anything not clearly visible into `uncertainties` (e.g.
"wheel design not visible"). If the car is clearly a different model (Swift,
Jimny, Vitara, Alto, Wagon R, Samurai, …), set ignis_confidence low and say so
in visible_negative_signals. Provide a one-line `visual_summary`."""


def should_run_vision(bucket: str, has_images: bool, manual: bool = False) -> bool:
    if not has_images:
        return False
    if manual:
        return True
    return bucket in _VISION_BUCKETS


def select_vision_images(image_urls: list[str], image_hashes: list[str] | None,
                         max_images: int = 5) -> list[str]:
    """Pick up to `max_images` diverse images. When perceptual hashes are known,
    drop near-duplicates so we never send 30 near-identical shots to Claude."""
    urls = [u for u in (image_urls or []) if u]
    if not urls:
        return []
    hashes = image_hashes or []
    if hashes and len(hashes) == len(urls):
        from ..deduplication.imagehashing import hamming
        kept: list[str] = []
        kept_hashes: list[str] = []
        for u, h in zip(urls, hashes):
            if any(hamming(h, kh) <= 6 for kh in kept_hashes):
                continue  # near-duplicate
            kept.append(u)
            kept_hashes.append(h)
            if len(kept) >= max_images:
                break
        if kept:
            return kept
    # No usable hashes: spread picks across the gallery (front/rear/side/interior)
    if len(urls) <= max_images:
        return urls
    step = max(1, len(urls) // max_images)
    return urls[::step][:max_images]


def vision_cache_key(image_urls: list[str], image_hashes: list[str] | None) -> str:
    """Stable key over the images used, so unchanged photos hit the cache."""
    basis = "|".join(image_hashes) if image_hashes else "|".join(image_urls or [])
    return content_hash("vision", basis)


def _download_image_b64(url: str, timeout: float) -> dict | None:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": get_settings().user_agent})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content or len(r.content) > 4_000_000:
        return None
    ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        ctype = "image/jpeg"
    return {"type": "image", "source": {
        "type": "base64", "media_type": ctype,
        "data": base64.standard_b64encode(r.content).decode("ascii")}}


def analyze_vehicle_images(image_urls: list[str], image_hashes: list[str] | None = None,
                           target_id: str | None = None,
                           max_images: int = 5) -> VisionVerdict:
    """Two-stage vision verdict. Falls back cleanly when AI/images unavailable."""
    client = get_client()
    if not client.enabled:
        return VisionVerdict(is_fallback=True,
                            visual_summary="Vision unavailable (no API key)")
    picks = select_vision_images(image_urls, image_hashes, max_images)
    if not picks:
        return VisionVerdict(is_fallback=True, visual_summary="No images to analyse")

    settings = get_settings()
    blocks: list[dict] = []
    for u in picks:
        b = _download_image_b64(u, settings.request_timeout)
        if b:
            blocks.append(b)
    if not blocks:
        return VisionVerdict(is_fallback=True, visual_summary="Images unfetchable")

    result = client.structured(
        module="image_detective", system=_SYSTEM,
        user="Analyse these listing photos (Stage A then Stage B).",
        schema=VisionVerdict, target_type="image", target_id=target_id,
        images=blocks, max_tokens=900)
    return result or VisionVerdict(is_fallback=True,
                                   visual_summary="Vision call failed")


def merge_vision(text_confidence: int, bucket: str, other_model: str | None,
                 has_hard_negative: bool, vision: VisionVerdict) -> dict:
    """Fuse text/technical confidence with the visual verdict.

    Rules (vision corroborates, never blindly overrides a conflict):
      * If the TITLE named another model but the image looks like an Ignis →
        DATA_CONFLICT (parser/listing mismatch), keep text confidence.
      * Vision only lifts confidence once it CONFIRMS Ignis identity
        (ignis_confidence ≥ 60). This is what unmasks bare "Suzuki Ignis" ads.
      * A hard technical negative (wrong power/displacement/new-gen year) caps
        how far vision may lift.
    """
    reasons: list[str] = []
    conflict = False
    final = text_confidence

    if vision.is_fallback:
        return {"final_confidence": final, "classification": None,
                "conflict": False, "reasons": ["vision unavailable"]}

    identity_ok = vision.ignis_confidence >= 60

    if other_model and vision.ignis_confidence >= 70:
        conflict = True
        reasons.append(
            f"Title says '{other_model}' but images look like an Ignis "
            f"(ignis {vision.ignis_confidence}%) — DATA_CONFLICT, needs review")
        return {"final_confidence": final, "classification": "DATA_CONFLICT",
                "conflict": True, "reasons": reasons}

    if not identity_ok:
        reasons.append(f"Images did not confirm Ignis (ignis {vision.ignis_confidence}%)")
        if bucket in ("POSSIBLE_IGNIS_SPORT",) and vision.ignis_confidence <= 20:
            final = max(0, text_confidence - 10)
            reasons.append("Visual identity weak → slight downgrade (-10)")
        return {"final_confidence": final, "classification": None,
                "conflict": False, "reasons": reasons}

    # Vision confirms Ignis → let the visual Sport score contribute.
    vs = vision.ignis_sport_visual_confidence
    visual_derived = round(min(97, 45 + vs * 0.5))  # vs 90→90, 70→80, 50→70
    if has_hard_negative:
        visual_derived = min(visual_derived, 70)
        reasons.append("Technical negative present → visual boost capped at 70")
    final = max(text_confidence, visual_derived)
    reasons.append(
        f"Vision confirms Ignis ({vision.ignis_confidence}%), sport visual "
        f"{vs}% → confidence {final}")
    return {"final_confidence": int(max(0, min(100, final))),
            "classification": None, "conflict": False, "reasons": reasons}
