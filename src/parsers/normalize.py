"""Deterministic extraction of prices, mileages, power, year, displacement and
currency from free text. This is exactly the kind of work that should NEVER go
to an LLM — it's cheap, fast and testable here."""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Currency handling. Static fallback rates (approximate, updated infrequently);
# the goal is comparability across countries, not accounting precision.
# ---------------------------------------------------------------------------
CURRENCY_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "CHF": 1.05,
    "GBP": 1.17,
    "PLN": 0.23,
    "CZK": 0.040,
    "HUF": 0.0025,
    "RON": 0.20,
    "BGN": 0.51,
    "DKK": 0.134,
    "SEK": 0.088,
    "NOK": 0.086,
}

_CURRENCY_SYMBOLS = {
    "€": "EUR", "eur": "EUR", "euro": "EUR",
    "chf": "CHF", "fr.": "CHF",
    "£": "GBP", "gbp": "GBP",
    "zł": "PLN", "zl": "PLN", "pln": "PLN",
    "kč": "CZK", "kc": "CZK", "czk": "CZK",
    "ft": "HUF", "huf": "HUF",
    "lei": "RON", "ron": "RON",
    "лв": "BGN", "bgn": "BGN",
    "kr": "SEK", "sek": "SEK", "dkk": "DKK", "nok": "NOK",
    "$": "EUR",  # rare on EU listings; treat conservatively as EUR-ish
}


def _to_number(raw: str) -> float | None:
    """Parse a European or Anglo formatted number: 12.500 / 12,500 / 12 500.
    Returns a float or None."""
    s = raw.strip()
    s = re.sub(r"[^\d.,\s]", "", s)
    s = s.replace("\xa0", " ").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # The rightmost separator is the decimal one.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Comma is decimal if exactly 1-2 trailing digits, else thousands sep.
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        # Only dots: dot is thousands sep unless it looks like a decimal.
        if not re.search(r"\.\d{1,2}$", s):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def detect_currency(text: str) -> str | None:
    t = text.lower()
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in t:
            return code
    return None


# Currency tokens for locating a price adjacent to a currency marker.
_CURRENCY_TOKEN = (r"€|eur\b|euro|chf|£|gbp|zł|zl\b|pln|kč|czk|ft\b|huf|"
                   r"lei|ron|лв|bgn|kr\b|sek|dkk|nok")
_NUM = r"[\d][\d.,\s\xa0]*\d|\d"
# Numbers that are actually mileage/power, which must not be read as a price.
_UNIT_NUM = re.compile(r"\d[\d.,\s\xa0]*\s*(?:km|tkm|miles|mi|kw|ps|hp|cv|ccm|cc)\b",
                       re.I)


def parse_price(text: str | None, default_currency: str | None = None
                ) -> tuple[float | None, str | None]:
    """Return (amount, currency_code).

    Prefers a number adjacent to a currency marker so that a mileage like
    "120.000 km" next to a price "4.750 €" never wins. Falls back to the
    largest plausible number only when no currency-adjacent value is found.
    """
    if not text:
        return None, None
    currency = detect_currency(text) or default_currency
    # Remove mileage/power figures so they can't be mistaken for a price.
    cleaned = _UNIT_NUM.sub(" ", text)

    candidates: list[float] = []
    for pat in (rf"(?:{_CURRENCY_TOKEN})\s*({_NUM})",
                rf"({_NUM})\s*(?:{_CURRENCY_TOKEN})"):
        for m in re.finditer(pat, cleaned, re.I):
            v = _to_number(m.group(1))
            if v is not None:
                candidates.append(v)

    if not candidates:  # no currency-adjacent number: fall back to largest.
        for c in re.findall(r"\d[\d.,\s\xa0]{2,}\d|\d{3,}", cleaned):
            v = _to_number(c)
            if v is not None:
                candidates.append(v)

    plausible = [v for v in candidates if 100 <= v <= 500_000]
    if not plausible:
        return None, currency
    return max(plausible), currency


# Phrases meaning "price on request" → price is genuinely unknown.
_ON_REQUEST = (
    "op aanvraag", "prijs op aanvraag", "auf anfrage", "preis auf anfrage",
    "price on request", "poa", "prezzo su richiesta", "prix sur demande",
    "precio a consultar", "sob consulta", "na dotaz", "cena dohodou",
)


def price_sanity(price_eur: float | None, text: str | None,
                 has_authoritative_offer: bool = False) -> tuple[float | None, str]:
    """Sanity-check an extracted EUR price. Returns (price_eur, status) where
    status ∈ OK / SUSPECT / UNKNOWN.

    A real used car is never €1 — such values are placeholders, financing
    figures, image counters or index numbers, not the sale price. Unless a
    clearly authoritative source vouches for it, an implausible price becomes
    UNKNOWN so downstream scoring never treats it as a bargain.
    """
    t = (text or "").lower()
    if any(p in t for p in _ON_REQUEST):
        return None, "UNKNOWN"
    if price_eur is None:
        return None, "UNKNOWN"
    if price_eur < 200:
        # €1/€100 placeholders, monthly-payment fragments, etc.
        return (price_eur, "SUSPECT") if has_authoritative_offer else (None, "SUSPECT")
    if price_eur > 250_000:
        return (price_eur, "SUSPECT") if has_authoritative_offer else (None, "SUSPECT")
    return price_eur, "OK"


def to_eur(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    rate = CURRENCY_TO_EUR.get((currency or "EUR").upper())
    if rate is None:
        return None
    return round(amount * rate, 2)


def parse_mileage(text: str | None) -> int | None:
    """Extract a kilometre reading. Handles '85.000 km', '85,000km', '85000'."""
    if not text:
        return None
    t = text.lower().replace("\xa0", " ")
    m = re.search(r"(\d[\d.,\s]{1,9}\d|\d{2,7})\s*(km|tkm|miles|mi\b|000\s*km)", t)
    if not m:
        return None
    num = _to_number(m.group(1))
    if num is None:
        return None
    unit = m.group(2)
    if "tkm" in unit:
        num *= 1000
    if "mile" in unit or unit.strip() == "mi":
        num *= 1.60934
    if 0 <= num <= 1_000_000:
        return int(round(num))
    return None


def parse_power(text: str | None) -> tuple[int | None, int | None]:
    """Return (kw, hp). Accepts 'kW', 'PS', 'HP', 'CV', 'CH', 'pk'. Converts
    between kW and hp when only one is given (1 kW = 1.35962 hp)."""
    if not text:
        return None, None
    t = text.lower().replace("\xa0", " ")
    kw = hp = None
    mkw = re.search(r"(\d{2,3})\s*kw", t)
    if mkw:
        kw = int(mkw.group(1))
    mhp = re.search(r"(\d{2,3})\s*(ps|hp|cv|ch|pk|bhp)\b", t)
    if mhp:
        hp = int(mhp.group(1))
    if kw and not hp:
        hp = int(round(kw * 1.35962))
    if hp and not kw:
        kw = int(round(hp / 1.35962))
    return kw, hp


def parse_year(text: str | None) -> int | None:
    """Find a plausible production/registration year (1990..current+1)."""
    if not text:
        return None
    now = datetime.now(timezone.utc).year
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", text)]
    plausible = [y for y in years if 1990 <= y <= now + 1]
    if not plausible:
        return None
    # Prefer the earliest plausible (registration year over "posted 2026").
    return min(plausible)


def parse_displacement(text: str | None) -> int | None:
    """Extract engine displacement in cc. '1.5', '1490cc', '1490 ccm'."""
    if not text:
        return None
    t = text.lower().replace("\xa0", " ")
    m = re.search(r"(\d{3,4})\s*(?:cc|ccm|cm3|cm³)", t)
    if m:
        cc = int(m.group(1))
        if 500 <= cc <= 8000:
            return cc
    m2 = re.search(r"\b(\d[.,]\d)\s*l?\b", t)
    if m2:
        litres = float(m2.group(1).replace(",", "."))
        if 0.5 <= litres <= 8.0:
            return int(round(litres * 1000))
    return None


# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


# Very small language guesser based on stopword hits — good enough to tag a
# description's language for the analyst / translation step. Not authoritative.
_LANG_HINTS = {
    "de": (" und ", " der ", " mit ", "gebraucht", "verkauf"),
    "fr": (" et ", " avec ", " occasion", " vendre", " voiture"),
    "it": (" e ", " con ", " usata", " vendita", " auto "),
    "es": (" y ", " con ", " venta", " segunda mano", " coche"),
    "nl": (" en ", " met ", " te koop", " tweedehands"),
    "pl": (" i ", " sprzedam", " używany", " samochód"),
    "en": (" and ", " with ", " for sale", " used ", " car "),
}


def detect_language(text: str | None) -> str | None:
    if not text:
        return None
    t = f" {text.lower()} "
    scores = {lang: sum(t.count(h) for h in hints) for lang, hints in _LANG_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None
