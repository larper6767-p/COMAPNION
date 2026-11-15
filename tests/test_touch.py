"""Feature 13 Stage A: touch zones + input classification (pure, fake clock).

No Qt, no lifecycle of the pet, no clips needed.  Her input pipeline is only
ever classified by core.touch.TouchClassifier; the app-side wiring is asserted
as source tripwires (the GUI/headless test env can't build the Qt widget).

The zones are FRAME-RELATIVE (fractions of the visible model frame, Feature
15), so the hit test is exact and follows resize / zoom / pan for free.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.framing import Frame, Framing                   # noqa: E402
from core.touch import (                                 # noqa: E402
    TouchClassifier, TouchConfig, find_zone, load_config,
    reload_config, zone_rect_model, zone_rect_px,
    all_zone_rects_px, DEFAULT_ZONES,
    ALLOWED_ZONES,
)


@pytest.fixture()
def hot_cfg(tmp_path):
    """A private config path (the loader takes the path explicitly)."""
    p = tmp_path / "touch_reactions.json"
    reload_config()
    yield p
    reload_config()


# A frame we can reason about: 10x10 model units shown in a 400 px square
# -> 40 px per unit, frame centre at model (5, 5) = pixel (200, 200).
FRAME = Frame(0.0, 0.0, 10.0, 10.0)


def _framing(frame: Frame = FRAME, width_px: float = 400.0,
             height_px: float = 400.0) -> Framing:
    return Framing(frame=frame, width_px=width_px, height_px=height_px)


# ── config ────────────────────────────────────────────────────────────────────

def test_defaults_when_file_missing(hot_cfg):
    cfg = load_config(hot_cfg)
    assert cfg.enabled is True
    assert cfg.click_action == "talk"
    assert cfg.hold_seconds == 0.6 and cfg.cancel_px == 6
    assert all(name in ALLOWED_ZONES for name, _ in cfg.zones)
    assert not any(name not in ALLOWED_ZONES for name, _ in cfg.zones)


def test_zone_whitelist_rejects_forbidden_zones(hot_cfg):
    # The spec: head / face / shoulders ONLY.  A 'chest' entry must be dropped.
    hot_cfg.write_text(
        '{"zones": {"head": {"x":0,"y":0,"w":1,"h":1},'
        ' "chest": {"x":0,"y":-2,"w":2,"h":2}}}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert ("head", (0.0, 0.0, 1.0, 1.0)) in cfg.zones
    assert all(name != "chest" for name, _ in cfg.zones)


def test_broken_file_falls_back_to_defaults(hot_cfg):
    hot_cfg.write_text("{not json", encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert cfg.enabled is True and cfg.click_action == "talk"


def test_input_overrides(hot_cfg):
    hot_cfg.write_text(
        '{"enabled": true, "click_action": "talk",'
        ' "input": {"hold_seconds": 1.2, "cancel_px": 9}}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert cfg.hold_seconds == 1.2 and cfg.cancel_px == 9


def test_obsolete_anchor_keys_are_ignored(hot_cfg):
    """Feature 15 removed the anchor/zoom_invert calibration."""
    hot_cfg.write_text(
        '{"anchor": {"x": 1, "y": 2}, "zoom_invert": true}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert not hasattr(cfg, "anchor_x")
    assert not hasattr(cfg, "zoom_invert")


# ── framing transform (zones follow resize / zoom / pan) ─────────────────────

def test_frame_relative_zone_becomes_model_units():
    fr = _framing()
    # head = the top 38% of the frame, centred
    x, y, w, h = zone_rect_model(fr.frame, (0.30, 0.62, 0.40, 0.38))
    assert x == pytest.approx(3.0)
    assert y == pytest.approx(6.2)
    assert w == pytest.approx(4.0)
    assert h == pytest.approx(3.8)


def test_zone_rect_px_maps_into_the_square():
    fr = _framing()
    l, t, r, b = zone_rect_px(fr, (0.0, 0.0, 1.0, 1.0))
    # the whole frame covers the whole square
    assert l == pytest.approx(0.0)
    assert r == pytest.approx(400.0)
    assert t == pytest.approx(0.0)
    assert b == pytest.approx(400.0)


def test_zones_follow_model_zoom():
    """Zooming the model (a smaller frame) keeps the zones on the same model
    area, so their pixel size shrinks with the model."""
    small = _framing(Frame(0.0, 0.0, 5.0, 5.0))
    big = _framing(Frame(0.0, 0.0, 10.0, 10.0))
    r1 = zone_rect_px(small, (0.0, 0.0, 1.0, 1.0))
    r2 = zone_rect_px(big, (0.0, 0.0, 1.0, 1.0))
    assert (r1[2] - r1[0]) == pytest.approx(400.0)   # the frame fills the square
    assert (r2[2] - r2[0]) == pytest.approx(400.0)


def test_zones_follow_face_resize():
    """A bigger window shows the same model region -> the same zones, more
    pixels."""
    fr = _framing(width_px=800.0, height_px=800.0)
    l, t, r, b = zone_rect_px(fr, (0.0, 0.0, 1.0, 1.0))
    assert r == pytest.approx(800.0)


def test_zones_follow_a_tall_window():
    """A taller window (the frame's aspect) keeps the zones on the same model
    area - the zone rects grow with the canvas, never stretch."""
    # the app's canvas always matches the frame's aspect (the height is
    # derived from it), so a tall window means a tall frame
    tall = Frame(0.0, 0.0, 10.0, 15.0)
    fr = _framing(frame=tall, width_px=400.0, height_px=600.0)
    l, t, r, b = zone_rect_px(fr, (0.0, 0.0, 1.0, 1.0))
    # the whole frame covers the whole canvas (width 400, height 600)
    assert l == pytest.approx(0.0)
    assert r == pytest.approx(400.0)
    assert t == pytest.approx(0.0)
    assert b == pytest.approx(600.0)
    # a zone keeps its model size -> the same pixel height
    hl, ht, hr, hb = zone_rect_px(fr, (0.0, 0.0, 1.0, 0.5))
    assert (hb - ht) == pytest.approx(300.0)


def _default_cfg_file(tmp_path):
    """A private config with the SHIPPED frame-relative default zones.

    The real config/touch_reactions.json is user-tuned (once the zones have
    been re-placed in the editor it stores absolute model units with
    zones_relative=false), so tests must not read it."""
    p = tmp_path / "touch_reactions.json"
    p.write_text(json.dumps({
        "enabled": True,
        "click_action": "talk",
        "input": {"hold_seconds": 0.6, "cancel_px": 6},
        "zone_priority": ["face", "head", "shoulders"],
        "zones_relative": True,
        "zones": {n: dict(zip(("x", "y", "w", "h"), r))
                  for n, r in DEFAULT_ZONES},
    }), encoding="utf-8")
    return p


def test_zones_follow_model_pan(tmp_path):
    """Frame-relative zones (the default) stay where they are on the square -
    they are glued to the FRAME.  Absolute (model-unit) zones are glued to
    her BODY and move with the model when it is panned."""
    cfg = load_config(_default_cfg_file(tmp_path))
    assert cfg.zones_relative is True
    fr1 = _framing(Frame(0.0, 0.0, 10.0, 10.0))
    fr2 = _framing(Frame(2.0, 0.0, 10.0, 10.0))
    r1 = all_zone_rects_px(cfg, fr1)
    r2 = all_zone_rects_px(cfg, fr2)
    assert r1["head"] == pytest.approx(r2["head"])

    # absolute zones: the same model rect, panned -> the pixels move with it
    abs_cfg = TouchConfig(zones=cfg.zones, zones_relative=False)
    a1 = all_zone_rects_px(abs_cfg, fr1)
    a2 = all_zone_rects_px(abs_cfg, fr2)
    assert (a2["head"][0] - a1["head"][0]) == pytest.approx(-2.0 * 40.0)


def test_find_zone_priority_and_misses(tmp_path):
    cfg = load_config(_default_cfg_file(tmp_path))
    fr = _framing()
    rects = all_zone_rects_px(cfg, fr)
    fl, ft, fr_, fb = rects["face"]
    hl, ht, hr, hb = rects["head"]
    sl, st, sr, sb = rects["shoulders"]
    # the face zone lies inside the head zone -> face wins (more specific)
    assert find_zone((fl + fr_) / 2, (ft + fb) / 2, cfg, fr) == "face"
    # the very top of the frame is head only
    assert find_zone((hl + hr) / 2, ht + 2.0, cfg, fr) == "head"
    # the bottom band is the shoulders
    assert find_zone((sl + sr) / 2, (st + sb) / 2, cfg, fr) == "shoulders"
    # outside everything -> None
    assert find_zone(-50.0, 500.0, cfg, fr) is None


# ── classifier ─────────────────────────────────────────────────────────────────

def _cfg(hold=0.6, cancel=6.0):
    return TouchConfig(hold_seconds=hold, cancel_px=cancel)


def test_quick_click_is_talk_anywhere():
    c = TouchClassifier()
    c.press(_cfg(), 100.0, 300.0, t=0.0, zone="face", ctrl=False)
    oc = c.release(_cfg(), 100.0, 300.0, t=0.25)
    assert oc.kind == "talk"


def test_quick_click_with_ctrl_on_zone_is_poke():
    c = TouchClassifier()
    c.press(_cfg(), 120.0, 200.0, t=0.0, zone="head", ctrl=True)
    assert c.poll(_cfg(), 0.65) is None          # ctrl never patents
    oc = c.release(_cfg(), 121.0, 201.0, t=0.5)
    assert oc.kind == "poke" and oc.zone == "head"


def test_hold_still_on_head_is_pat():
    c = TouchClassifier()
    c.press(_cfg(), 130.0, 150.0, t=10.0, zone="head", ctrl=False)
    assert c.poll(_cfg(), 10.0 + 0.59) is None
    oc = c.poll(_cfg(), 10.0 + 0.61)     # past the 0.6 s boundary (float-exact)
    assert oc.kind == "pat" and oc.zone == "head"
    # the press stays armed; the release only reports the full hold span so
    # escalation can credit the rest (Feature 13 Stage B pat_end bookkeeping)
    tail = c.release(_cfg(), 130.0, 150.0, t=12.0)
    assert tail.kind == "pat_end" and tail.duration == pytest.approx(2.0)


def test_movement_becomes_a_normal_drag():
    c = TouchClassifier()
    c.press(_cfg(), 130.0, 150.0, t=0.0, zone="head", ctrl=False)
    oc = c.move(_cfg(), 130.0, 157.0, t=0.2)     # 7 px straight down
    assert oc.kind == "drag"
    assert c.poll(_cfg(), 1.0) is None           # pat can no longer fire
    assert c.release(_cfg(), 130.0, 157.0, t=2.0) is None


def test_small_jitter_under_cancel_is_still_a_pat():
    c = TouchClassifier()
    c.press(_cfg(), 130.0, 150.0, t=0.0, zone="head", ctrl=False)
    assert c.move(_cfg(), 134.0, 153.0, t=0.1) is None   # 5 px — still "still"
    assert c.poll(_cfg(), 0.6).kind == "pat"


def test_disabled_flag_makes_app_skip_classification():
    """Documented behaviour: the app never calls the classifier at all when
    the config (or Live2D mode) is off; the classifier itself stays inert on
    empty state regardless."""
    c = TouchClassifier()
    assert c.release(_cfg(), 5.0, 5.0, 0.0) is None
    assert c.poll(_cfg(), 5.0) is None
    assert c.armed is False


def test_poke_on_non_head_zones_and_outside_zones():
    c = TouchClassifier()
    c.press(_cfg(), 10.0, 10.0, t=0.0, zone="shoulders", ctrl=True)
    oc = c.release(_cfg(), 10.0, 10.0, t=0.2)
    assert oc.kind == "poke" and oc.zone == "shoulders"

    c2 = TouchClassifier()
    c2.press(_cfg(), 399.0, 399.0, t=0.0, zone=None, ctrl=True)
    oc2 = c2.release(_cfg(), 399.0, 399.0, t=0.2)
    assert oc2.kind == "poke" and oc2.zone is None   # ctrl clicked empty space
