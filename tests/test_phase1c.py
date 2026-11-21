"""Phase 1c tests: apply_body_actions / _tick_body in Live2DWidget.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_phase1c.py -v

These exercise ONLY pure dict/timer logic — no GL context, no QApplication,
no live2d model.  We build a bare instance with object.__new__ and give it the
state each body method touches, then drive apply_body_actions -> _tick_body ->
_flush_params against a fake model that records SetSaveParamById.

Not (headless) testable here: actual rendering and live2d GetParam*ById reads.
"""
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class FakeModel:
    """Records SetSaveParamById calls like the live2d Model."""
    def __init__(self):
        self.params = {}
        self.calls = []
        self.ranges = {}      # param -> (min, max, default) for expression ops

    def SetSaveParamById(self, p, v, w):
        self.params[p] = v
        self.calls.append(("SetSaveParamById", p, v))

    def GetParamMinById(self, p):
        return self.ranges[p][0]

    def GetParamMaxById(self, p):
        return self.ranges[p][1]

    def GetParamDefaultById(self, p):
        return self.ranges[p][2]


def make_widget(max_fx=3, wink_swap=False, look_invert_x=False, body_enabled=True):
    """Bare Live2DWidget with the state the body methods touch.

    Mirrors the state set in __init__ for body-control effects plus the params
    _flush_params needs (_current/_proc).  Must specify a NON-None _model so the
    guards `self._model is None` in apply_body_actions/_tick_body/_flush_params
    all pass.
    """
    from live2d_face import Live2DWidget, _BASELINE

    w = Live2DWidget.__new__(Live2DWidget)
    w._current = dict(_BASELINE)
    w._target = dict(_BASELINE)
    w._proc = {}
    w._smooth_k = 4.9
    w._body_enabled = bool(body_enabled)
    w._wink_swap = bool(wink_swap)
    w._look_invert_x = bool(look_invert_x)
    w._max_simultaneous_fx = int(max_fx)
    w._active_fx = []          # [{name, params, ends_at}]
    w._fx_overrides = {}       # param -> value this frame
    w._gestures = []           # [{kind, mode, params, start, ends_at}]
    w._model = FakeModel()
    return w


# ── 1. fx stages and auto-reverts ─────────────────────────────────────────────

def test_fx_staged_with_ends_at():
    w = make_widget()
    now = time.monotonic()
    before = now
    actions = [{"kind": "fx", "name": "blush", "duration": 4.0,
                "params": {"ParamSwitch8": 1.0}}]
    # Monkey-patch time.monotonic is tricky; instead assert the stored ends_at is
    # in the future and close to now + duration (use a tolerance).
    w.apply_body_actions(actions)
    assert len(w._active_fx) == 1
    fx = w._active_fx[0]
    assert fx["name"] == "blush"
    assert fx["params"] == {"ParamSwitch8": 1.0}
    assert fx["ends_at"] > before + 3.0


def test_fx_auto_reverts_on_tick_expiry():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    # Move the fx's ends_at into the past to simulate expiry.
    w._active_fx[0]["ends_at"] = time.monotonic() - 0.1
    w._tick_body(1 / 30, time.monotonic())
    assert w._active_fx == []
    assert w._fx_overrides == {}


def test_fx_rebuilds_overrides_each_tick():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    now = time.monotonic()
    w._tick_body(1 / 30, now)
    assert w._fx_overrides == {"ParamSwitch8": 1.0}


def test_fx_cap_drops_new_fx_when_full():
    w = make_widget(max_fx=3)
    for i in range(3):
        w.apply_body_actions([{"kind": "fx", "name": f"fx{i}", "duration": 4.0,
                               "params": {f"ParamSwitch{i}": 1.0}}])
    assert len(w._active_fx) == 3
    # 4th fx must be dropped (cap): _active_fx stays at 3.
    w.apply_body_actions([{"kind": "fx", "name": "over", "duration": 4.0,
                           "params": {"ParamSwitch99": 1.0}}])
    assert len(w._active_fx) == 3
    assert not any(f["name"] == "over" for f in w._active_fx)


def test_fx_write_precedes_mood_base_in_flush():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    w._tick_body(1 / 30, time.monotonic())
    w._flush_params()
    # The mood base for ParamSwitch8 would be 0.0 but fx must win -> 1.0.
    assert w._model.params["ParamSwitch8"] == pytest.approx(1.0)
    assert w._model.calls[0][0] == "SetSaveParamById"


# ── Phase 3c: exp3 expression ops (blend + clamp, no SetExpression) ──────────

def test_expression_ops_blend_and_clamp():
    """Add = default+value, Overwrite = value, Multiply = default*value, all
    clamped to the model's real [min, max] range.  Never calls SetExpression."""
    w = make_widget()
    w._model.ranges = {
        "ParamSwitch8": (0.0, 1.0, 0.0),
        "ParamBrowRForm": (-1.0, 1.0, 0.0),
        "ParamBreath": (0.0, 1.0, 0.5),
    }
    out = w._resolve_expression_params([
        {"id": "ParamSwitch8", "value": 1.0, "blend": "Add"},        # 0+1 = 1
        {"id": "ParamBrowRForm", "value": 5.0, "blend": "Add"},     # 0+5 -> clamp 1
        {"id": "ParamBreath", "value": 0.5, "blend": "Multiply"},   # 0.5*0.5 = 0.25
        {"id": "ParamSwitch8", "value": 0.5, "blend": "Overwrite"}, # 0.5 wins last
    ])
    assert out["ParamSwitch8"] == pytest.approx(0.5)
    assert out["ParamBrowRForm"] == pytest.approx(1.0)
    assert out["ParamBreath"] == pytest.approx(0.25)


def test_expression_fx_apply_merges_params():
    """An fx with expression_ops drives its params through the normal fx path
    (no SetExpression)."""
    w = make_widget()
    w._model.ranges = {"ParamSwitch8": (0.0, 1.0, 0.0)}
    w.apply_body_actions([{
        "kind": "fx", "name": "heart_eyes", "duration": 4.0,
        "expression_ops": [{"id": "ParamSwitch8", "value": 1.0, "blend": "Add"}],
        "params": {},
    }])
    assert w._active_fx[0]["params"]["ParamSwitch8"] == pytest.approx(1.0)
    w._tick_body(1 / 30, time.monotonic())
    w._flush_params()
    assert w._model.params["ParamSwitch8"] == pytest.approx(1.0)


def test_expression_explicit_params_win():
    """If an action has both explicit params and expression ops, the explicit
    value wins for a param both touch."""
    w = make_widget()
    w._model.ranges = {"ParamSwitch8": (0.0, 1.0, 0.0)}
    w.apply_body_actions([{
        "kind": "fx", "name": "demo", "duration": 4.0,
        "expression_ops": [{"id": "ParamSwitch8", "value": 1.0, "blend": "Add"}],
        "params": {"ParamSwitch8": 0.0},
    }])
    assert w._active_fx[0]["params"]["ParamSwitch8"] == pytest.approx(0.0)


# ── 2. look/gesture writ additively with fade envelope ────────────────────────

def test_look_writes_additive_into_proc():
    w = make_widget(look_invert_x=False)
    w.apply_body_actions([{"kind": "look", "name": "left", "duration": 0.9,
                           "params": {"ParamAngleX": -18.0}}])
    now = time.monotonic()
    # Advance well into the gesture so the fade-in envelope reaches ~1.0.
    w._tick_body(1 / 30, now + 0.5)
    # proc has the look contribution (additive, envelope-scaled).
    assert "ParamAngleX" in w._proc
    assert abs(w._proc["ParamAngleX"] + 18.0) < 0.5  # near -18 (env -> 1)


def test_look_invert_x_negates_angle_and_eyeball():
    w = make_widget(look_invert_x=True)
    w.apply_body_actions([{"kind": "look", "name": "left", "duration": 0.9,
                           "params": {"ParamAngleX": 18.0,
                                      "ParamEyeBallX": 0.5}}])
    now = time.monotonic()
    w._tick_body(1 / 30, now + 0.5)
    # look_invert_x flips both ParamAngleX and ParamEyeBallX signs.
    assert w._proc["ParamAngleX"] < 0
    assert w._proc["ParamEyeBallX"] < 0


def test_gesture_additive_combines():
    w = make_widget()
    w.apply_body_actions([{"kind": "gesture", "name": "nod", "duration": 0.9,
                           "params": {"ParamAngleY": 10.0}}])
    now = time.monotonic()
    w._proc["ParamAngleY"] = 0.0
    w._tick_body(1 / 30, now + 0.5)
    assert w._proc["ParamAngleY"] == pytest.approx(10.0, abs=0.5)


def test_gesture_expires_removed_from_proc():
    w = make_widget()
    w.apply_body_actions([{"kind": "gesture", "name": "nod", "duration": 0.9,
                           "params": {"ParamAngleY": 10.0}}])
    now = time.monotonic()
    w._tick_body(1 / 30, now + 0.5)
    assert "ParamAngleY" in w._proc
    # Expire: move ends_at into the past, clear the frame's proc layer (as
    # paintGL does at frame start), then tick — gesture removed, proc clear.
    w._gestures[0]["ends_at"] = time.monotonic() - 0.1
    w._proc.clear()
    w._tick_body(1 / 30, time.monotonic())
    assert w._gestures == []
    assert "ParamAngleY" not in w._proc


# ── 3. wink override writes 0.0 to the chosen eye ─────────────────────────────

def test_wink_override_write_zero_to_side_eye():
    w = make_widget(wink_swap=False)
    w.apply_body_actions([{"kind": "wink", "side": "right", "duration": 0.5}])
    now = time.monotonic()
    w._tick_body(1 / 30, now + 0.2)
    # Right side (SCREEN side) -> ParamEyeROpen closed to ~0.0 (envelope);
    # ParamEyeLOpen untouched by this gesture.
    assert "ParamEyeROpen" in w._proc
    assert abs(w._proc["ParamEyeROpen"]) <= 0.01
    assert "ParamEyeLOpen" not in w._proc


def test_wink_swap_maps_left_eye():
    w = make_widget(wink_swap=True)
    w.apply_body_actions([{"kind": "wink", "side": "right", "duration": 0.5}])
    now = time.monotonic()
    w._tick_body(1 / 30, now + 0.2)
    # wink_swap flips the mapping: 'right' -> ParamEyeLOpen closed.
    assert "ParamEyeLOpen" in w._proc
    assert abs(w._proc["ParamEyeLOpen"]) <= 0.01
    assert "ParamEyeROpen" not in w._proc


# ── 4. [clear] empties everything ─────────────────────────────────────────────

def test_clear_empties_fx_and_gestures():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    w.apply_body_actions([{"kind": "gesture", "name": "nod", "duration": 0.9,
                           "params": {"ParamAngleY": 10.0}}])
    assert w._active_fx and w._gestures
    w.apply_body_actions([{"kind": "clear"}])
    assert w._active_fx == []
    assert w._gestures == []
    assert w._fx_overrides == {}


# ── 5. Guards: no model / body disabled → no-op ───────────────────────────────

def test_apply_noop_when_body_disabled():
    w = make_widget(body_enabled=False)
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    assert w._active_fx == []


def test_apply_noop_when_model_none():
    w = make_widget()
    w._model = None
    w.apply_body_actions([{"kind": "fx", "name": "blush", "duration": 4.0,
                           "params": {"ParamSwitch8": 1.0}}])
    assert w._active_fx == []


def test_tick_noop_when_body_disabled():
    w = make_widget(body_enabled=False)
    w.apply_body_actions([{"kind": "look", "name": "left", "duration": 0.9,
                           "params": {"ParamAngleX": 18.0}}])
    w._tick_body(1 / 30, time.monotonic())
    assert w._proc == {}


# ── 6. Real config: blush switch mapping + hand gestures ──────────────────────
# The fx definitions live in config/body_actions.json (and the bundled defaults)
# and are consumed by _resolve_body_actions in app.py and the Test-body-action
# menu / preview tool.  These tests lock in the switch mapping so the model's real
# rig is respected (blush = ParamSwitch2, not the heart-eyes switch ParamSwitch8).


def test_config_blush_uses_real_blush_switch():
    from body_tags import actions
    ba = actions()
    # Per the model's cdi3 + 脸红.exp3.json, blush is ParamSwitch2, NOT
    # ParamSwitch8 (which is the heart-eyes switch).
    assert ba.fx["blush"]["params"].get("ParamSwitch2") == pytest.approx(1.0)
    assert "ParamSwitch8" not in ba.fx["blush"]["params"]


def test_config_defines_hand_gestures():
    from body_tags import actions
    ba = actions()
    # 右手 (right hand) = ParamSwitch10, 左手 (left hand) = ParamSwitch11,
    # per the model's 右手.exp3.json / 左手.exp3.json.  The switch params
    # stay defined regardless; whether they are ENABLED is user-owned
    # (previewed and toggled in config/body_actions.json) — the model/menu
    # listing must always agree with the enabled flag.
    for side, switch in (("hand_right", "ParamSwitch10"),
                         ("hand_left", "ParamSwitch11")):
        entry = ba.fx[side]
        assert entry["params"] == {switch: 1.0}
        assert isinstance(entry["enabled"], bool)
        assert (f"fx:{side}" in ba.enabled_capped_actions()) is entry["enabled"]


def test_config_expression_actions_expand_once():
    """Phase 3c: fx with an "expression" key expand into plain param ops at load
    (single source = the .exp3.json files), and never call SetExpression."""
    from body_tags import actions
    ba = actions()
    for name, switch in (("star_eyes", "ParamSwitch5"),
                         ("heart_eyes", "ParamSwitch8"),
                         ("white_eyes", "ParamSwitch6")):
        cfg = ba.fx[name]
        assert cfg["enabled"] is True
        ops = cfg.get("expression_ops") or []
        assert ops, name                      # expanded at config load
        assert any(op["id"] == switch and op["value"] == 1.0 for op in ops), \
            (name, switch)
        assert all(op.get("blend") for op in ops)
    # they are exposed to the menu / preview
    for key in ("fx:star_eyes", "fx:heart_eyes", "fx:white_eyes"):
        assert key in ba.enabled_capped_actions()


def test_hand_fx_apply_to_model():
    w = make_widget()
    for name, switch in (("hand_right", "ParamSwitch10"),
                         ("hand_left", "ParamSwitch11")):
        w.apply_body_actions([{"kind": "fx", "name": name, "duration": 2.5,
                               "params": {switch: 1.0}}])
    w._tick_body(1 / 30, time.monotonic())
    assert w._fx_overrides == {"ParamSwitch10": 1.0, "ParamSwitch11": 1.0}
    w._flush_params()
    assert w._model.params["ParamSwitch10"] == pytest.approx(1.0)
    assert w._model.params["ParamSwitch11"] == pytest.approx(1.0)


def test_config_defines_face_dark_and_blood():
    from body_tags import actions
    ba = actions()
    # face-dark = ParamSwitch1, blood = ParamSwitch3 (per 脸黑.exp3.json / 血.exp3.json).
    assert ba.fx["face_dark"]["enabled"] is True
    assert ba.fx["face_dark"]["params"] == {"ParamSwitch1": 1.0}
    assert ba.fx["blood"]["enabled"] is True
    assert ba.fx["blood"]["params"] == {"ParamSwitch3": 1.0}


def test_face_dark_blood_apply_to_model():
    w = make_widget()
    for name, switch in (("face_dark", "ParamSwitch1"),
                         ("blood", "ParamSwitch3")):
        w.apply_body_actions([{"kind": "fx", "name": name, "duration": 2.5,
                               "params": {switch: 1.0}}])
    w._tick_body(1 / 30, time.monotonic())
    assert w._fx_overrides == {"ParamSwitch1": 1.0, "ParamSwitch3": 1.0}
    w._flush_params()
    assert w._model.params["ParamSwitch1"] == pytest.approx(1.0)
    assert w._model.params["ParamSwitch3"] == pytest.approx(1.0)


def test_face_dark_blood_auto_revert_to_baseline():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "face_dark", "duration": 2.5,
                           "params": {"ParamSwitch1": 1.0}}])
    now = time.monotonic()
    w._tick_body(1 / 30, now)
    assert w._fx_overrides == {"ParamSwitch1": 1.0}
    w._active_fx[0]["ends_at"] = now - 0.1
    w._tick_body(1 / 30, now + 0.1)
    assert w._fx_overrides == {}
    w._flush_params()
    assert w._model.params["ParamSwitch1"] == pytest.approx(0.0)


def test_hand_fx_auto_revert_returns_hand_to_baseline():
    w = make_widget()
    w.apply_body_actions([{"kind": "fx", "name": "hand_right", "duration": 2.5,
                           "params": {"ParamSwitch10": 1.0}}])
    now = time.monotonic()
    w._tick_body(1 / 30, now)
    assert w._fx_overrides == {"ParamSwitch10": 1.0}
    # Expire the fx -> the override is dropped, so ParamSwitch10 falls back to
    # the mood base (0.0 = hand down) on the next flush.
    w._active_fx[0]["ends_at"] = now - 0.1
    w._tick_body(1 / 30, now + 0.1)
    assert w._fx_overrides == {}
    w._flush_params()
    assert w._model.params["ParamSwitch10"] == pytest.approx(0.0)
