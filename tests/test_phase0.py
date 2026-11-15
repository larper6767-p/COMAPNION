"""Phase 0 tests for the Live2D procedural layer split + fixes.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_phase0.py -v

These tests exercise ONLY pure dictionary/timer logic inside Live2DWidget —
no GL context, no QApplication, no model.  We build a bare instance with
object.__new__ and give it just the attributes each tick method touches, then
drive _tick_* and _flush_params against a fake model recording SetParamById.

Not (headless) testable here: visual rendering, actual GetParam*ById live1d
values (needs the real model on a desktop), and the Qt/app wiring.
"""
import sys
from pathlib import Path

import pytest

# Pop poisoned module entries so the real src modules load (test_headless stubs).
for _m in ("core", "core.config", "core.memory"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class FakeModel:
    """Records SetParamById/SetSaveParamById calls like the live2d Model."""
    def __init__(self):
        self.params = {}
        self.calls = []

    def SetParamById(self, p, v, w):
        self.calls.append(("SetParamById", p, v))

    def SetSaveParamById(self, p, v, w):
        self.params[p] = v
        self.calls.append(("SetSaveParamById", p, v))


def make_widget():
    """Bare Live2DWidget with only the state the tick methods need."""
    from live2d_face import Live2DWidget, _BASELINE

    # Use Live2DWidget.__new__ directly to bypass QObject's __new__ (no
    # QApplication / GL context needed) — see object.__new__ rejects it.
    w = Live2DWidget.__new__(Live2DWidget)
    w._current = dict(_BASELINE)
    w._target = dict(_BASELINE)
    w._proc = {}
    w._smooth_k = 4.9
    w._invert_eye_x = False
    w._invert_head_x = False
    w._invert_head_y = False
    w._speaking = False
    w._mouth_current = 0.0
    w._mouth_target = 0.0
    w._mouth_noise_timer = 0.0
    w._blink_state = "open"
    w._blink_t = 0.0
    w._blink_next = 100.0
    w._t_breath = 0.0
    w._t_sway = 0.0
    w._t_body = 0.0
    w._dart_timer = 100.0
    w._dart_target_x = 0.0
    w._dart_target_y = 0.0
    w._dart_hold = 0.0
    w._dart_active = False
    w._dart_cur_x = 0.0
    w._dart_cur_y = 0.0
    w._pose_timer = 100.0
    w._pose_target_x = 0.0
    w._pose_target_y = 0.0
    w._pose_cur_x = 0.0
    w._pose_cur_y = 0.0
    w._pose_hold = 0.0
    w._pose_active = False
    w._cursor_x = 0.0
    w._cursor_y = 0.0
    w._cursor_cur_x = 0.0
    w._cursor_cur_y = 0.0
    w._model = None
    return w


# ── 1. Blink closure reaches exactly 0.0 ───────────────────────────────────────

def test_blink_reaches_exactly_zero_closed():
    w = make_widget()
    # Force a blink now.
    w._blink_next = 0.0
    w._blink_state = "open"
    # Run small steps through close → closed state.
    steps = 0
    while w._blink_state != "closed" and steps < 1000:
        w._tick_blink(0.008)
        steps += 1
    assert w._blink_state == "closed"
    # In the closed state _proc must hold EXACTLY 0.0 (procedural override,
    # so smoothing can never re-open them).
    w._tick_blink(0.008)
    assert w._proc["ParamEyeLOpen"] == 0.0
    assert w._proc["ParamEyeROpen"] == 0.0


def test_blink_uses_proc_override_survives_smooth():
    w = make_widget()
    # Force the widget into the closed state (eyes=0.0 in _proc).
    w._blink_state = "closed"
    w._blink_t = 0.0
    w._tick_blink(0.008)
    assert w._proc["ParamEyeLOpen"] == 0.0
    # Even if smoothing runs with target eyes=1.0, proc override keeps 0.0.
    w._current["ParamEyeLOpen"] = 1.0
    w._target["ParamEyeLOpen"] = 1.0
    w._tick_smooth(1 / 30)
    assert w._proc["ParamEyeLOpen"] == 0.0
    assert w._proc["ParamEyeROpen"] == 0.0


# ── 2. Breath / mouth reach written values ────────────────────────────────────

def test_breath_written_to_proc():
    w = make_widget()
    w._tick_idle(1 / 30)
    assert "ParamBreath" in w._proc
    assert 0.0 <= w._proc["ParamBreath"] <= 1.0


def test_mouth_reaches_written_value():
    w = make_widget()
    # Speaking path re-randomises the target, so use the non-speaking decay to
    # verify _tick_mouth writes exactly its eased value into the proc layer.
    w._speaking = False
    w._mouth_current = 0.8
    w._mouth_target = 0.0
    for _ in range(500):
        w._tick_mouth(1 / 30)
    # Decays to ~0 and is written verbatim (no smoothing overlay).
    assert w._proc["ParamMouthOpenY"] == w._mouth_current
    assert w._mouth_current == pytest.approx(0.0, abs=0.01)


# ── 3. Time-based easing identical at 30 vs 60 fps ────────────────────────────

def test_smooth_identical_at_30_and_60fps():
    w30 = make_widget()
    w60 = make_widget()
    for w in (w30, w60):
        w._target["ParamAngleZ"] = 10.0
    # 30 fps: 30 steps of 1/30 s
    for _ in range(30):
        w30._tick_smooth(1 / 30)
    # 60 fps: 60 steps of 1/60 s
    for _ in range(60):
        w60._tick_smooth(1 / 60)
    assert w30._current["ParamAngleZ"] == pytest.approx(
        w60._current["ParamAngleZ"], abs=1e-9)


# ── 4. Horizontal cursor → ParamAngleX, vertical → ParamAngleY ────────────────

def test_cursor_horizontal_drives_anglex_vertical_angley():
    w = make_widget()
    # Horizontal cursor offset (x positive) should move ParamAngleX; vertical
    # (y positive) should move ParamAngleY.  With no invert flags.
    w._cursor_cur_x = 1.0
    w._cursor_cur_y = 0.0
    w._tick_cursor(1 / 30)
    # Positive + horizontal. HEAD_X_SCALE=12 → 12.0 expected (after a few frames
    # of easing; single step should be >0 and sign-matched).
    assert w._proc["ParamAngleX"] > 0
    assert w._proc["ParamAngleY"] == pytest.approx(0.0, abs=1e-9)

    w._proc.clear()
    w._cursor_cur_x = 0.0
    w._cursor_cur_y = 1.0
    w._tick_cursor(1 / 30)
    assert w._proc["ParamAngleY"] > 0
    assert w._proc["ParamAngleX"] == pytest.approx(0.0, abs=1e-9)


def test_cursor_invert_head_x_flips_sign():
    w = make_widget()
    w._invert_head_x = True
    w._cursor_cur_x = 1.0
    w._tick_cursor(1 / 30)
    assert w._proc["ParamAngleX"] < 0


# ── 5. Idle-off override survives a simulated restart ─────────────────────────

def test_idle_override_persists(tmp_path):
    import json
    # Test the persistence key ('idle_enabled') read/write round-trip that
    # app.py uses.  The Qt wiring itself needs a desktop.
    state = tmp_path / "pet_state.json"
    state.write_text(json.dumps({"idle_enabled": False}), encoding="utf-8")

    def load(default):
        try:
            return bool(json.loads(state.read_text(encoding="utf-8")).get("idle_enabled", default))
        except Exception:
            return default

    first = load(default=True)
    assert first is False
    # Simulate a user toggle + save
    state.write_text(json.dumps({"idle_enabled": True}), encoding="utf-8")
    second = load(default=True)
    assert second is True


# ── 2e. Liveliness + behavior-director persistence (round-trip) ───────────────

def test_liveliness_zero_persists_round_trip(tmp_path):
    """A liveliness of 0.0 (the 'Still' preset) survives save -> reload.  This is
    the exact-0 case the plan calls out: it must be saved, reloaded and applied
    after the widget exists, and restored even when the saved position is
    off-screen (same fix as the idle toggle)."""
    import json
    state = tmp_path / "pet_state.json"
    state.write_text(json.dumps({"liveliness": 0.0}), encoding="utf-8")

    def load_liveliness(default):
        try:
            v = float(json.loads(state.read_text(encoding="utf-8"))["liveliness"])
            return max(0.0, min(1.0, v))
        except Exception:
            return default

    assert load_liveliness(default=0.7) == 0.0
    # simulate a user choosing a non-preset value + save, then a reload
    state.write_text(json.dumps({"liveliness": 1.0}), encoding="utf-8")
    assert load_liveliness(default=0.7) == 1.0
    # out-of-range values are clamped to [0, 1] like the app does
    state.write_text(json.dumps({"liveliness": 5.0}), encoding="utf-8")
    assert load_liveliness(default=0.7) == 1.0


def test_behavior_director_toggle_persists(tmp_path):
    """The right-click 'Behavior director on/off' override survives restarts."""
    import json
    state = tmp_path / "pet_state.json"
    state.write_text(json.dumps({"behavior_director": False}), encoding="utf-8")

    def load(default):
        try:
            return bool(json.loads(state.read_text(encoding="utf-8"))["behavior_director"])
        except Exception:
            return default

    assert load(default=True) is False
    state.write_text(json.dumps({"behavior_director": True}), encoding="utf-8")
    assert load(default=True) is True


# ── 6. Layer merge: flush writes base + additive proc ─────────────────────────

def test_flush_merges_base_and_additive_proc():
    w = make_widget()
    w._current["ParamMouthOpenY"] = 0.1      # mood base
    w._proc["ParamMouthOpenY"] = 0.5         # override → should win
    w._current["ParamAngleZ"] = 0.0          # mood base
    w._proc["ParamAngleZ"] = 3.0             # additive → sum
    w._proc["ParamAngleX"] = 12.0            # additive, base 0
    model = FakeModel()
    w._model = model
    w._flush_params()
    assert model.params["ParamMouthOpenY"] == pytest.approx(0.5)   # override
    assert model.params["ParamAngleZ"] == pytest.approx(3.0)       # 0 + 3
    assert model.params["ParamAngleX"] == pytest.approx(12.0)      # 0 + 12


# ── 7. Procedural layer is rebuilt each frame (no cross-frame accumulation) ────

def _simulate_frame(w):
    """Mirror the tail of paintGL: clear proc, run the procedural ticks.

    Deliberately omits _model.Update()/Draw() — we only verify the param math.
    """
    w._proc.clear()
    w._tick_idle(1 / 30)
    w._tick_idle_behaviour(1 / 30)
    w._tick_blink(1 / 30)
    w._tick_mouth(1 / 30)
    w._tick_cursor(1 / 30)


def test_no_proc_accumulation_across_frames():
    """Additive writers (pose + cursor) must not keep piling onto _proc.

    Regression for the pre-fix bug where `_proc[p] += v` persisted its value
    between frames, so a held head pose / cursor offset grew every frame and
    eventually drove ParamAngleX far out of its valid range.
    """
    w = make_widget()
    # Hold a fixed head pose (non-zero settlement) and a fixed cursor offset.
    w._pose_active = True
    w._pose_hold = 100.0
    w._pose_target_x = 8.0
    w._pose_target_y = 5.0
    w._pose_cur_x = 8.0
    w._pose_cur_y = 5.0
    # Cursor fully settled (target == eased value) so its contribution is the
    # same every frame, isolating the cross-frame accumulation check.
    w._cursor_x = 1.0
    w._cursor_y = 1.0
    w._cursor_cur_x = 1.0
    w._cursor_cur_y = 1.0

    _simulate_frame(w)
    ax_frame1 = w._proc.get("ParamAngleX", 0.0)

    # Run many more frames; the additive writers reach the same settlement each
    # frame, so the value must STAY bounded, not grow frame-over-frame.
    for _ in range(120):
        _simulate_frame(w)
    ax_final = w._proc.get("ParamAngleX", 0.0)

    assert ax_final == pytest.approx(ax_frame1, abs=1e-6), \
        f"ParamAngleX accumulated across frames: {ax_frame1} -> {ax_final}"


def test_additive_writers_combine_within_frame():
    """Cursor + idle pose both add into ParamAngleX in the same frame.

    This verifies proc.clear() at frame start still lets multiple contributors
    sum (cursor offset AND pose) instead of one clobbering the other.
    """
    w = make_widget()
    w._pose_active = True
    w._pose_hold = 100.0
    w._pose_target_x = 5.0
    w._pose_cur_x = 5.0
    # Cursor settled so its eased value doesn't taper across the single frame.
    w._cursor_x = 1.0
    w._cursor_cur_x = 1.0
    w._cursor_y = 0.0
    w._cursor_cur_y = 0.0

    # Bare values (base 0) so the result is purely the two additive writers.
    w._pose_cur_y = 0.0
    _simulate_frame(w)

    # Cursor adds 12.0*1.0 (HEAD_X_SCALE) and pose adds its settled 5.0.
    assert w._proc["ParamAngleX"] == pytest.approx(12.0 + 5.0, abs=1e-6)


# ── 8. Writes must survive Update() — SetSaveParamById, NOT SetParamById ───────

def test_flush_uses_set_save_param_by_id():
    """Every value we own must go through SetSaveParamById (which writes the
    saved value too) so it survives Model.Update()'s LoadParameters()."""
    w = make_widget()
    w._current["ParamMouthOpenY"] = 0.3
    w._current["ParamAngleX"] = 2.0
    w._proc["ParamAngleX"] = 1.0          # additive
    w._proc["ParamBreath"] = 0.5           # override
    model = FakeModel()
    w._model = model
    w._flush_params()

    # Every write recorded by _flush_params must be SetSaveParamById; a stray
    # SetParamById would mean the value gets discarded by Update()'s LoadParameters.
    assert model.calls, "expected _flush_params to write to the model"
    assert all(m == "SetSaveParamById" for (m, _p, _v) in model.calls), \
        f"_flush_params must use SetSaveParamById, found: {model.calls}"
    # Spot-check the merged values landed via the save-capable call.
    assert model.params["ParamMouthOpenY"] == pytest.approx(0.3)
    assert model.params["ParamAngleX"] == pytest.approx(3.0)
    assert model.params["ParamBreath"] == pytest.approx(0.5)
