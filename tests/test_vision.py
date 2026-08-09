"""Claude Vision tests: gating (never on other models), image selection/dedup,
the text+vision merge (incl. DATA_CONFLICT), and the image cache preventing
repeated API calls."""

from __future__ import annotations

import src.pipeline.ingest as ing
from src.ai.schemas import VisionVerdict
from src.ai.vision import (
    merge_vision,
    select_vision_images,
    should_run_vision,
)
from src.parsers.html_generic import RawListing


# --- Gating ----------------------------------------------------------------
def test_vision_gating():
    assert should_run_vision("POSSIBLE_IGNIS_SPORT", True) is True
    assert should_run_vision("UNKNOWN", True) is True
    assert should_run_vision("NORMAL_IGNIS", True) is True          # bare Ignis
    assert should_run_vision("OTHER_MODEL", True) is False
    assert should_run_vision("IRRELEVANT", True) is False
    assert should_run_vision("CLEAR_IGNIS_SPORT", True) is False    # already sure
    assert should_run_vision("POSSIBLE_IGNIS_SPORT", False) is False  # no images
    assert should_run_vision("OTHER_MODEL", True, manual=True) is True  # manual override


# --- Image selection / dedup ----------------------------------------------
def test_select_vision_images_dedups_near_duplicates():
    urls = ["a.jpg", "b.jpg", "c.jpg"]
    hashes = ["ffffffffffffffff", "fffffffffffffffe", "0000000000000000"]  # a≈b
    picks = select_vision_images(urls, hashes, max_images=5)
    assert "a.jpg" in picks and "c.jpg" in picks
    assert "b.jpg" not in picks  # near-duplicate of a removed


def test_select_vision_images_caps_count():
    urls = [f"{i}.jpg" for i in range(30)]
    assert len(select_vision_images(urls, None, max_images=5)) <= 5


# --- Merge logic -----------------------------------------------------------
def test_merge_vision_confirms_bare_ignis_as_sport():
    v = VisionVerdict(ignis_confidence=96, ignis_sport_visual_confidence=90)
    out = merge_vision(20, "NORMAL_IGNIS", None, False, v)
    assert out["final_confidence"] >= 80  # bare "Suzuki Ignis" lifted by vision


def test_merge_vision_uncertain_does_not_boost():
    v = VisionVerdict(ignis_confidence=40, ignis_sport_visual_confidence=80)
    out = merge_vision(20, "POSSIBLE_IGNIS_SPORT", None, False, v)
    assert out["final_confidence"] <= 20  # identity not confirmed → no boost


def test_merge_vision_conflict_flags_data_conflict():
    v = VisionVerdict(ignis_confidence=85, ignis_sport_visual_confidence=80)
    out = merge_vision(0, "OTHER_MODEL", "swift", False, v)
    assert out["classification"] == "DATA_CONFLICT"
    assert out["conflict"] is True


def test_merge_vision_hard_negative_caps_boost():
    v = VisionVerdict(ignis_confidence=95, ignis_sport_visual_confidence=95)
    out = merge_vision(20, "POSSIBLE_IGNIS_SPORT", None, True, v)
    assert out["final_confidence"] <= 70


# --- End-to-end gating + cache in ingest ----------------------------------
def _fake_vision_factory(counter):
    def _fake(image_urls, image_hashes=None, target_id=None, max_images=5):
        counter["n"] += 1
        return VisionVerdict(vehicle_identity_confidence=95, ignis_confidence=96,
                             ignis_sport_visual_confidence=88,
                             visible_positive_signals=["sport bumper", "side skirts"],
                             visual_summary="Looks like an Ignis Sport")
    return _fake


def test_other_model_never_invokes_vision(session, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ing, "analyze_vehicle_images", _fake_vision_factory(calls))
    monkeypatch.setattr(ing, "perceptual_hashes", lambda urls, **k: [])
    raw = RawListing(title="Suzuki Swift Sport 1.6", url="https://d.de/swift-1",
                     source_domain="d.de", images=["https://d.de/1.jpg"], country="DE")
    res = ing.process_candidate(raw, None)
    assert res.listing is None            # hard-rejected before any AI
    assert calls["n"] == 0                # vision never called for an other model


def test_vision_boost_and_cache(session, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ing, "analyze_vehicle_images", _fake_vision_factory(calls))
    monkeypatch.setattr(ing, "perceptual_hashes", lambda urls, **k: [])
    raw = RawListing(title="Suzuki Ignis", url="https://d.de/ignis-1",
                     source_domain="d.de", images=["https://d.de/1.jpg"], country="DE")

    r1 = ing.process_candidate(raw, None)
    assert calls["n"] == 1
    assert r1.listing.vision_analyzed is True
    assert r1.listing.ignis_sport_visual_confidence == 88
    # A bare "Suzuki Ignis" is lifted to a real Sport candidate by the images.
    assert r1.listing.vehicle_match_confidence >= 80

    # Same images again → cache reuse, NO second API call.
    ing.process_candidate(raw, None)
    assert calls["n"] == 1
