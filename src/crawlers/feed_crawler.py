"""RSS / Atom / XML feed crawling. Preferred over HTML whenever a source
exposes a feed — cheaper, cleaner and change-friendly."""

from __future__ import annotations

import feedparser

from ..parsers.html_generic import RawListing
from ..parsers.normalize import (
    parse_displacement,
    parse_mileage,
    parse_power,
    parse_price,
    parse_year,
)
from ..utils.hashing import domain_of
from ..utils.logging import get_logger
from .base import HttpFetcher

log = get_logger("crawler.feed")


def crawl_feed(fetcher: HttpFetcher, feed_url: str,
               country: str | None = None) -> list[RawListing]:
    res = fetcher.fetch(feed_url)
    if not res.ok or not res.text:
        return []
    parsed = feedparser.parse(res.text)
    domain = domain_of(feed_url)
    out: list[RawListing] = []
    for entry in parsed.entries:
        title = getattr(entry, "title", "") or ""
        link = getattr(entry, "link", "") or feed_url
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        text = f"{title} {summary}"
        price, currency = parse_price(text)
        kw, hp = parse_power(text)
        out.append(RawListing(
            title=title[:200] or link,
            url=link,
            source_domain=domain,
            description=summary,
            price=price,
            currency=currency,
            mileage_km=parse_mileage(text),
            year=parse_year(text),
            power_kw=kw,
            power_hp=hp,
            displacement_cc=parse_displacement(text),
            country=country,
            extraction_method="feed",
            raw_text=text,
        ))
    log.info("feed %s -> %d entries", feed_url, len(out))
    return out
