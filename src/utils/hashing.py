"""Hashing / URL helpers used for deduplication, caching and stable IDs."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


def content_hash(*parts: str | None) -> str:
    """Stable SHA-256 over the given text parts (None treated as empty)."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").strip().lower().encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()


def stable_id(*parts: str | None) -> str:
    """Short deterministic id (first 20 hex chars of the content hash)."""
    return content_hash(*parts)[:20]


# Tracking params we strip so the "same" URL dedups cleanly.
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$)", re.I)


def normalize_url(url: str) -> str:
    """Canonicalise a URL: lowercase host, drop fragment and tracking params,
    strip trailing slash. Keeps meaningful query params (listing ids etc.)."""
    if not url:
        return url
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if p.port:
        netloc = f"{host}:{p.port}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
             if not _TRACKING.match(k)]
    query.sort()
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme or "https", netloc, path, "", urlencode(query), ""))


def is_specific_url(url: str | None) -> bool:
    """True if the URL points at a specific page (has a real path or query),
    not a bare homepage like https://dealer.com/ . Used to guarantee we never
    store a homepage as the canonical/original vehicle link when a concrete
    listing URL is known."""
    if not url:
        return False
    try:
        p = urlparse(url if "//" in url else f"https://{url}")
    except ValueError:
        return False
    path = (p.path or "").strip("/")
    return bool(path) or bool(p.query)


def pick_canonical(urls: list[str] | None, dealer_domain: str | None = None,
                   aggregators: tuple[str, ...] = ()) -> str | None:
    """Choose the best concrete vehicle URL from candidates.

    Preference: dealer-direct specific listing → any specific non-aggregator
    listing → any specific listing → first URL as a last resort. A homepage is
    only ever chosen if nothing more specific exists.
    """
    cands = [u for u in (urls or []) if u]
    if not cands:
        return None
    specific = [u for u in cands if is_specific_url(u)]

    if dealer_domain:
        for u in specific:
            if domain_of(u) == dealer_domain:
                return u
    non_agg = [u for u in specific
               if not any(a in domain_of(u) for a in aggregators)]
    if non_agg:
        return non_agg[0]
    if specific:
        return specific[0]
    return cands[0]


def domain_of(url: str) -> str:
    """Registered-ish domain (host without leading www)."""
    try:
        host = urlparse(url if "//" in url else f"//{url}", scheme="https").hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host
