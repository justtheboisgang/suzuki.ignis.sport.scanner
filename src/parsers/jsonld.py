"""Extract vehicle/product data from JSON-LD and microdata. Many dealer sites
(WordPress + schema plugins, AutoScout-style pages) embed schema.org Vehicle or
Product objects — the cleanest structured source available before falling back
to loose HTML parsing."""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup


def _iter_objects(node: Any):
    """Walk arbitrarily nested JSON-LD graphs yielding dict objects."""
    if isinstance(node, dict):
        if "@graph" in node and isinstance(node["@graph"], list):
            for item in node["@graph"]:
                yield from _iter_objects(item)
        yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _iter_objects(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_objects(item)


_VEHICLE_TYPES = {"car", "vehicle", "product", "offer", "motorizedvehicle"}


def extract_jsonld_vehicles(html: str) -> list[dict]:
    """Return a list of normalised dicts for any Vehicle/Product/Offer nodes."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _iter_objects(data):
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = [t] if isinstance(t, str) else (t or [])
            if not any(str(x).lower() in _VEHICLE_TYPES for x in types):
                continue
            parsed = _normalise_node(obj)
            if parsed.get("title") or parsed.get("price") or parsed.get("url"):
                results.append(parsed)
    return results


def _first(*vals):
    for v in vals:
        if v:
            return v
    return None


def _normalise_node(obj: dict) -> dict:
    offers = obj.get("offers")
    price = currency = None
    if isinstance(offers, list):
        # offers may be a (possibly nested) list; use the first dict we find.
        offers = next((o for o in offers if isinstance(o, dict)), None)
    if isinstance(offers, dict):
        price = offers.get("price")
        currency = offers.get("priceCurrency")

    engine = obj.get("vehicleEngine")
    power = None
    displacement = None
    if isinstance(engine, dict):
        power = _extract_qty(engine.get("enginePower"))
        displacement = _extract_qty(engine.get("engineDisplacement"))

    return {
        "title": _first(obj.get("name"), obj.get("model")),
        "brand": _brand(obj.get("brand")) or obj.get("manufacturer"),
        "model": obj.get("model"),
        "price": _num(price),
        "currency": currency,
        "url": _first(obj.get("url"), obj.get("@id")),
        "year": obj.get("vehicleModelDate") or obj.get("productionDate")
                or obj.get("modelDate"),
        "mileage": _extract_qty(obj.get("mileageFromOdometer")),
        "power_raw": power,
        "displacement_raw": displacement,
        "description": obj.get("description"),
        "images": _images(obj.get("image")),
        "color": obj.get("color"),
        "vin": obj.get("vehicleIdentificationNumber"),
        "fuel": obj.get("fuelType"),
        "transmission": obj.get("vehicleTransmission"),
    }


def _brand(b):
    if isinstance(b, dict):
        return b.get("name")
    return b


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _extract_qty(v):
    if isinstance(v, dict):
        return v.get("value") or v.get("name")
    return v


def _images(v):
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [v.get("url") or v.get("contentUrl")] if (v.get("url") or v.get("contentUrl")) else []
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                u = item.get("url") or item.get("contentUrl")
                if u:
                    out.append(u)
        return out
    return []
