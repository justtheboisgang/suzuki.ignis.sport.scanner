"""Tests for JSON-LD and generic HTML extraction on realistic fixtures."""

from src.parsers.html_generic import extract_listings_from_html
from src.parsers.jsonld import extract_jsonld_vehicles

JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Car",
  "name": "Suzuki Ignis 1.5 Sport",
  "brand": {"@type": "Brand", "name": "Suzuki"},
  "vehicleModelDate": "2005",
  "mileageFromOdometer": {"@type": "QuantitativeValue", "value": "118000", "unitCode": "KMT"},
  "vehicleEngine": {"@type": "EngineSpecification",
      "enginePower": "80 kW", "engineDisplacement": "1490 ccm"},
  "color": "red",
  "offers": {"@type": "Offer", "price": "4900", "priceCurrency": "EUR"},
  "url": "https://autohaus-mueller.de/fahrzeug/ignis-sport-123",
  "image": ["https://autohaus-mueller.de/img/1.jpg"]
}
</script></head><body>Suzuki Ignis Sport</body></html>
"""

ANCHOR_PAGE = """
<html><body>
<ul>
  <li><a href="/inventar/suzuki-ignis-sport-2005">Suzuki Ignis Sport 2005</a>
      1.5 VVT, 80 kW, 120.000 km, 4.750 €</li>
  <li><a href="/inventar/vw-polo">VW Polo 1.4</a> nice car</li>
</ul>
</body></html>
"""


def test_jsonld_extraction():
    vehicles = extract_jsonld_vehicles(JSONLD_PAGE)
    assert len(vehicles) == 1
    v = vehicles[0]
    assert "Ignis" in v["title"]
    assert v["price"] == 4900.0
    assert v["currency"] == "EUR"


def test_jsonld_offers_as_list_does_not_crash():
    # Regression: a page whose "offers" is a list (or a nested list) crashed the
    # WHOLE discovery pass with "'list' object has no attribute 'get'".
    list_offer = JSONLD_PAGE.replace(
        '"offers": {"@type": "Offer", "price": "4900", "priceCurrency": "EUR"}',
        '"offers": [{"@type": "Offer", "price": "4900", "priceCurrency": "EUR"}]')
    v = extract_jsonld_vehicles(list_offer)
    assert v and v[0]["price"] == 4900.0

    nested = JSONLD_PAGE.replace(
        '"offers": {"@type": "Offer", "price": "4900", "priceCurrency": "EUR"}',
        '"offers": [[{"price": "4900"}]]')
    # Must not raise; price simply can't be read from a malformed nested list.
    assert extract_jsonld_vehicles(nested)  # no exception is the point


def test_html_generic_uses_jsonld_first():
    listings = extract_listings_from_html(
        JSONLD_PAGE, "https://autohaus-mueller.de/", "autohaus-mueller.de", "DE")
    assert listings
    top = listings[0]
    assert top.extraction_method == "jsonld"
    assert top.price == 4900.0
    assert top.mileage_km == 118000
    assert top.power_kw == 80
    assert top.displacement_cc == 1490


def test_html_generic_card_isolation():
    # The extractor is model-agnostic: it returns one candidate PER card, each
    # with its OWN exact detail href and isolated text. (Non-Ignis models are
    # dropped later by the classifier, not by the extractor.)
    listings = extract_listings_from_html(
        ANCHOR_PAGE, "https://autohaus-mueller.de/", "autohaus-mueller.de", "DE")
    by_title = {l.title: l for l in listings}
    assert any("Ignis" in t for t in by_title)
    ignis = [l for l in listings if "Ignis" in l.title][0]
    # Its listing_url must be its OWN detail href, never the page/homepage.
    assert ignis.url.endswith("/inventar/suzuki-ignis-sport-2005")
    assert ignis.listing_url_quality in ("EXACT_DETAIL", "LIKELY_DETAIL")
    assert ignis.card_href_found is True
    assert ignis.mileage_km == 120000
    assert ignis.price == 4750.0
    # The Ignis card's isolated text must NOT contain the VW Polo card's text.
    assert "Polo" not in ignis.combined_text()
