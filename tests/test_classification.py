"""Deterministic classification tests, incl. the hard Model Identity Gate:
an explicit OTHER Suzuki model can never score as an Ignis Sport, and the word
'Sport' alone never produces a Sport score."""

import pytest

from src.classification.confidence import score_confidence
from src.classification.prefilter import (
    CLEAR_IGNIS_SPORT,
    IRRELEVANT,
    NORMAL_IGNIS,
    OTHER_MODEL,
    POSSIBLE_IGNIS_SPORT,
    prefilter,
)
from src.models.enums import Classification


def _run(identity, context="", **kw):
    pf = prefilter(identity, context, **kw)
    cr = score_confidence(pf, **kw)
    return pf, cr


# --- Model Identity Gate (the critical fix) --------------------------------
@pytest.mark.parametrize("title", [
    "Suzuki Swift Sport 1.6",
    "Suzuki Jimny",
    "Suzuki Vitara",
    "Suzuki Grand Vitara 2.0",
    "Suzuki Wagon R+",
    "Suzuki Samurai SJ413",
    "Suzuki SJ410",
    "Suzuki Alto 1.0",
    "Suzuki Baleno",
    "Suzuki SX4 S-Cross",
    "Suzuki Celerio",
])
def test_other_suzuki_models_are_hard_rejected(title):
    pf, cr = _run(title, year=2006)
    assert pf.bucket == OTHER_MODEL
    assert pf.is_ignis is False
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


def test_swift_sport_even_with_sport_context_is_not_ignis():
    # 'Sport' + an Ignis mentioned elsewhere on the page must NOT leak in.
    pf, cr = _run("Suzuki Swift Sport 1.6",
                  context="Also in stock: Suzuki Ignis Sport HT81S")
    assert pf.bucket == OTHER_MODEL
    assert cr.confidence == 0


def test_non_suzuki_is_irrelevant():
    pf, cr = _run("Volkswagen Golf GTI 2.0 TSI")
    assert pf.bucket == IRRELEVANT
    assert cr.confidence == 0


# --- Ignis / Sport detection ----------------------------------------------
def test_explicit_ignis_sport_is_clear_and_high():
    pf, cr = _run("Suzuki Ignis Sport 1.5 VVT 2005")
    assert pf.bucket == CLEAR_IGNIS_SPORT
    assert not pf.needs_ai
    assert cr.confidence >= 80


def test_chassis_code_is_strong():
    pf, cr = _run("Rare Suzuki HT81S project car")
    assert pf.bucket == CLEAR_IGNIS_SPORT
    assert cr.confidence >= 80


def test_mislabelled_sport_routes_to_ai():
    pf, cr = _run("Suzuki Ignis 1.5 2004, 80 kW, Sportsitze, Spoiler",
                  year=2004, power_kw=80, displacement_cc=1490)
    assert pf.bucket == POSSIBLE_IGNIS_SPORT
    assert pf.needs_ai is True
    assert cr.confidence >= 40


def test_ignis_15_80kw_is_possible_sport():
    pf, cr = _run("Suzuki Ignis 1.5 80 kW", year=2005, power_kw=80)
    assert pf.is_ignis is True
    assert pf.bucket in (POSSIBLE_IGNIS_SPORT,)
    assert pf.needs_ai is True


def test_ignis_13_is_normal_not_sport():
    pf, cr = _run("Suzuki Ignis 1.3 GL Klima", year=2006, displacement_cc=1328)
    assert pf.is_ignis is True
    assert pf.bucket == NORMAL_IGNIS
    assert cr.confidence < 60
    assert cr.classification == Classification.NORMAL_IGNIS.value


def test_bare_ignis_is_not_automatic_sport():
    pf, cr = _run("Suzuki Ignis")
    assert pf.is_ignis is True
    assert cr.confidence < 60
    assert cr.classification != Classification.CONFIRMED_IGNIS_SPORT.value


def test_new_generation_ignis_excluded():
    pf, cr = _run("Suzuki Ignis 1.2 Dualjet Hybrid SHVS 2019",
                  year=2019, displacement_cc=1242)
    assert cr.confidence < 40


def test_confidence_monotone():
    _, low = _run("Suzuki Ignis 1.3 2007", year=2007, displacement_cc=1328)
    _, high = _run("Suzuki Ignis Sport HT81S 2005 1.5 80kW", year=2005,
                   power_kw=80, displacement_cc=1490)
    assert high.confidence > low.confidence
