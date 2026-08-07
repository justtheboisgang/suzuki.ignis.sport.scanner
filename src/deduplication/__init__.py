"""Cross-platform de-duplication: the same physical car listed on a dealer site,
AutoScout, mobile.de, a regional portal and Facebook must collapse into ONE
listing that keeps every known URL."""

from .imagehashing import perceptual_hashes, hamming, hashes_similar
from .dedup import find_duplicate, merge_into, dedup_key_signals

__all__ = [
    "perceptual_hashes",
    "hamming",
    "hashes_similar",
    "find_duplicate",
    "merge_into",
    "dedup_key_signals",
]
