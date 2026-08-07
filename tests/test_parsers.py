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


def test_html_generic_anchor_heuristic():
    listings = extract_listings_from_html(
        ANCHOR_PAGE, "https://autohaus-mueller.de/", "autohaus-mueller.de", "DE")
    titles = [l.title for l in listings]
    assert any("Ignis" in t for t in titles)
    # The VW Polo anchor must NOT be captured (no ignis/suzuki interest match).
    assert not any("Polo" in t for t in titles)
    ignis = [l for l in listings if "Ignis" in l.title][0]
    assert ignis.mileage_km == 120000
    assert ignis.price == 4750.0
