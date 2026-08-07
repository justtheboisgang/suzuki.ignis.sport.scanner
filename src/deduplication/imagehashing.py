"""Perceptual image hashing for cross-platform duplicate detection.

We store only compact hashes + URLs — never large image files. Pillow/ImageHash
are optional; if unavailable the functions degrade to returning no hashes (dedup
then relies on VIN/phone/text signals only).
"""

from __future__ import annotations

import io

import httpx

from ..config.settings import get_settings
from ..utils.logging import get_logger

log = get_logger("dedup.image")

try:
    import imagehash
    from PIL import Image
    _HAS_IMAGEHASH = True
except Exception:  # pragma: no cover
    _HAS_IMAGEHASH = False


def perceptual_hashes(image_urls: list[str], max_images: int = 4) -> list[str]:
    """Download a few images and return their pHash hex strings."""
    if not _HAS_IMAGEHASH or not image_urls:
        return []
    settings = get_settings()
    out: list[str] = []
    for url in image_urls[:max_images]:
        try:
            r = httpx.get(url, timeout=settings.request_timeout,
                          follow_redirects=True,
                          headers={"User-Agent": settings.user_agent})
            if r.status_code != 200 or not r.content:
                continue
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            out.append(str(imagehash.phash(img)))
        except Exception as exc:  # network/decoding issues are non-fatal
            log.debug("phash failed for %s: %s", url, exc)
    return out


def hamming(h1: str, h2: str) -> int:
    """Hamming distance between two hex phash strings (lower = more similar)."""
    try:
        a = int(h1, 16)
        b = int(h2, 16)
    except (ValueError, TypeError):
        return 999
    return bin(a ^ b).count("1")


def hashes_similar(set_a: list[str], set_b: list[str], threshold: int = 8) -> bool:
    """True if any image in A is perceptually close to any in B."""
    for h1 in set_a or []:
        for h2 in set_b or []:
            if hamming(h1, h2) <= threshold:
                return True
    return False
