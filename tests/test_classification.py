"""Tests for the deterministic pre-filter and confidence scorer — including the
crucial 'mislabelled Sport' detection path."""

from src.classification.confidence import score_confidence
from src.classification.prefilter import prefilter


def _pf_conf(text, **kw):
    pf = prefilter(text, **kw)
    cr = score_confidence(pf, **kw)
    return pf, cr


def test_explicit_sport_is_clear_and_high():
    pf, cr = _pf_conf("Suzuki Ignis Sport 1.5 VVT 2005")
    assert pf.bucket == "CLEAR_SPORT"
    assert not pf.needs_ai
    assert cr.confidence >= 80


def test_chassis_code_is_confirmed():
    pf, cr = _pf_conf("Rare Suzuki HT81S project car")
    assert pf.bucket == "CLEAR_SPORT"
    assert cr.confidence >= 90


def test_non_ignis_is_irrelevant():
    pf, cr = _pf_conf("Volkswagen Golf GTI 2.0 TSI")
    assert pf.bucket == "IRRELEVANT"
    assert cr.confidence == 0


def test_mislabelled_sport_routes_to_ai():
    # A bare Ignis with tell-tale 1.5 / 80 kW / sport equipment must be flagged
    # for the AI detective — this is the whole point of the project.
    pf, cr = _pf_conf("Suzuki Ignis 1.5 2004, 80 kW, Sportsitze, Spoiler",
                      year=2004, power_kw=80, displacement_cc=1490)
    assert pf.bucket == "NEEDS_AI"
    assert pf.needs_ai is True
    assert cr.confidence >= 55


def test_base_13_ignis_not_flagged_high():
    pf, cr = _pf_conf("Suzuki Ignis 1.3 GL Klima 2006",
                      year=2006, power_kw=61, displacement_cc=1328)
    assert cr.confidence < 60


def test_new_generation_ignis_excluded():
    pf, cr = _pf_conf("Suzuki Ignis 1.2 Dualjet Hybrid SHVS 2019",
                      year=2019, displacement_cc=1242)
    assert cr.confidence < 40


def test_confidence_bands_monotone():
    _, low = _pf_conf("Suzuki Ignis 1.3 2007", year=2007, displacement_cc=1328)
    _, high = _pf_conf("Suzuki Ignis Sport HT81S 2005 1.5 80kW", year=2005,
                       power_kw=80, displacement_cc=1490)
    assert high.confidence > low.confidence
