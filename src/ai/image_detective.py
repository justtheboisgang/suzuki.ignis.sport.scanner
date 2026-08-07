"""AI Module 4 — Image Vehicle Detective.

For interesting/uncertain candidates only, and only when images are legally
fetchable, look for visual Sport cues (spoiler, bumpers, side skirts, sport
wheels/seats, body kit, emblems). Always an *indicator*, never sole truth.
"""

from __future__ import annotations

import base64

import httpx

from ..config.settings import get_settings
from ..utils.logging import get_logger
from .client import get_client
from .schemas import ImageVerdict

log = get_logger("ai.image")

_SYSTEM = """You inspect photos of a Suzuki Ignis to judge whether it is the
Sport (HT81S) variant. Sport-specific visual cues: front/rear spoiler, deeper
sport front/rear bumpers, side skirts, 15" sport alloy wheels, sport seats,
'Sport' badging, body kit. The base Ignis lacks these. Report detected features
and a visual confidence. This is a supporting indicator only."""


def _download_image_b64(url: str, timeout: float) -> dict | None:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": get_settings().user_agent})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content:
        return None
    ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        ctype = "image/jpeg"
    if len(r.content) > 4_000_000:  # keep payloads modest
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": ctype,
            "data": base64.standard_b64encode(r.content).decode("ascii"),
        },
    }


def analyze_images(image_urls: list[str], target_id: str | None = None,
                   max_images: int = 3) -> ImageVerdict:
    client = get_client()
    if not client.enabled or not image_urls:
        return ImageVerdict(is_fallback=True,
                            notes="AI/image analysis unavailable or no images")
    settings = get_settings()
    blocks: list[dict] = []
    for url in image_urls[:max_images]:
        b = _download_image_b64(url, settings.request_timeout)
        if b:
            blocks.append(b)
    if not blocks:
        return ImageVerdict(is_fallback=True, notes="No images could be fetched")

    result = client.structured(
        module="image_detective",
        system=_SYSTEM,
        user="Do these photos show a Suzuki Ignis Sport? List visual cues.",
        schema=ImageVerdict,
        target_type="image",
        target_id=target_id,
        images=blocks,
        max_tokens=700,
    )
    return result or ImageVerdict(is_fallback=True, notes="AI image call failed")
