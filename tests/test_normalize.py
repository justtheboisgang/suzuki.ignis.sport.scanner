"""Tests for deterministic parsing/normalisation — prices, mileage, power,
year, displacement, currency conversion, language detection."""

import pytest

from src.parsers.normalize import (
    CURRENCY_TO_EUR,
    detect_language,
    parse_displacement,
    parse_mileage,
    parse_power,
    parse_price,
    parse_year,
    to_eur,
)


@pytest.mark.parametrize("text,expected", [
    ("Preis 4.900 EUR", 4900.0),
    ("€ 5.250,00", 5250.0),
    ("Price: 3,995 GBP", 3995.0),
    ("12 500 zł", 12500.0),
    ("nur 5900", 5900.0),
])
def test_parse_price_amount(text, expected):
    amount, _ = parse_price(text)
    assert amount == expected


def test_parse_price_currency_detection():
    assert parse_price("4.900 EUR")[1] == "EUR"
    assert parse_price("3995 GBP")[1] == "GBP"
    assert parse_price("12 500 zł")[1] == "PLN"


@pytest.mark.parametrize("text,expected", [
    ("120.000 km", 120000),
    ("85,000 km", 85000),
    ("km 99000", None),          # unit before number not matched -> None
    ("142000 km", 142000),
    ("12 tkm", 12000),
])
def test_parse_mileage(text, expected):
    assert parse_mileage(text) == expected


def test_parse_power_both_directions():
    assert parse_power("80 kW (109 PS)") == (80, 109)
    kw, hp = parse_power("109 PS")
    assert hp == 109 and 78 <= kw <= 82
    kw, hp = parse_power("80 kW")
    assert kw == 80 and 106 <= hp <= 112


@pytest.mark.parametrize("text,expected", [
    ("Erstzulassung 03/2005", 2005),
    ("Baujahr 2004", 2004),
    ("posted 2026, reg 2003", 2003),   # earliest plausible preferred
    ("no year here", None),
])
def test_parse_year(text, expected):
    assert parse_year(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1490 ccm", 1490),
    ("1.5 l", 1500),
    ("1,3 Liter", 1300),
])
def test_parse_displacement(text, expected):
    assert parse_displacement(text) == expected


def test_to_eur_conversion():
    assert to_eur(100, "EUR") == 100.0
    assert to_eur(100, "GBP") == round(100 * CURRENCY_TO_EUR["GBP"], 2)
    assert to_eur(None, "EUR") is None
    assert to_eur(100, "XYZ") is None  # unknown currency -> None, no guessing


def test_detect_language():
    assert detect_language("Auto zu verkaufen mit Scheckheft und Klima") == "de"
    assert detect_language("voiture d'occasion à vendre avec entretien") == "fr"
    assert detect_language("") is None
