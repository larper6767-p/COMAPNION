"""Feature 14: drag physics — pure sim + config tests.

The widget side (Live2DWidget) is asserted as source tripwires: there is no
GL context in the test env, so the physics itself is verified here with a
fake clock and a scripted drag.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.drag_physics import (                    # noqa: E402
    DragPhysics, DragPhysicsConfig, ParamSpec,
    load_config, reload_config,
    DEFAULT_CONFIG_PATH,
)


@pytest.fixture()
def hot_cfg(tmp_path):
    """A private config path.  The loader takes the path explicitly, so no
    module-level patching is needed (another test purges ``core`` from
    sys.modules, which breaks string-form monkeypatch targets)."""
    p = tmp_path / "drag_physics.json"
    reload_config()
    yield p
    reload_config()


def _cfg(**kw) -> DragPhysicsConfig:
    base = dict(
        params=(ParamSpec("HairFront_BS", "x", 1.0),
                ParamSpec("HairPinytail_SY1", "y", 0.8)),
    )
    base.update(kw)
    return DragPhysicsConfig(**base)


# ── config ───────────────────────────────────────────────────────────────────

def test_shipped_config_loads(hot_cfg):
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.enabled is True
    names = {p.name for p in cfg.params}
    assert "HairFront_BS" in names and "HanfFront_RS1" in names
    assert "Ear_SR1" in names and "BowSkirtBack_S1" in names
    # no body-angle params: the overlay must never touch the rig's inputs
    assert not any(n.startswith("Param") for n in names)


def test_missing_file_uses_defaults(hot_cfg):
    cfg = load_config(hot_cfg)
    assert cfg.enabled is True
    assert cfg.params                     # the default groups are present
    assert cfg.sign == -1.0


def test_broken_file_uses_defaults(hot_cfg):
    hot_cfg.write_text("{nope", encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert cfg.enabled is True and cfg.params


def test_bad_group_is_skipped_not_fatal(hot_cfg):
    hot_cfg.write_text(
        '{"enabled": true, "groups": {"a": {"axis": "x", "gain": 1.0,'
        ' "params": ["HairFront_BS"]}, "broken": "not an object"}}',
        encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert [p.name for p in cfg.params] == ["HairFront_BS"]


def test_duplicate_params_are_merged(hot_cfg):
    hot_cfg.write_text(
        '{"groups": {"a": {"axis": "x", "params": ["HairFront_BS"]},'
        ' "b": {"axis": "y", "params": ["HairFront_BS"]}}}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert len(cfg.params) == 1


def test_disabled_config_has_no_params_effect(hot_cfg):
    hot_cfg.write_text('{"enabled": false}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    sim = DragPhysics(cfg)
    assert sim.active is False
    assert sim.step(0.03, 5000.0, 0.0) == {}


# ── the sim ──────────────────────────────────────────────────────────────────

def test_constant_velocity_does_not_move_anything():
    """Only ACCELERATION moves hair - a steady glide must not swing it."""
    sim = DragPhysics(_cfg())
    for _ in range(60):
        sim.step(1 / 60.0, 0.0, 0.0)      # no acceleration at all
    assert all(abs(v) < 1e-9 for v in sim.offsets().values())


def test_sudden_motion_lags_behind_then_overshoots_and_settles():
    sim = DragPhysics(_cfg())
    # yank her to the right: constant acceleration for 0.2 s
    for _ in range(12):
        sim.step(1 / 60.0, 3000.0, 0.0)
    off = sim.offsets()["HairFront_BS"]
    assert off < 0.0                       # hair lags BEHIND the motion
    # keep moving at constant velocity (no more acceleration) -> it keeps
    # swinging, then overshoots the other way
    peak_neg = off
    overshot = False
    for _ in range(180):
        sim.step(1 / 60.0, 0.0, 0.0)
        v = sim.offsets()["HairFront_BS"]
        peak_neg = min(peak_neg, v)
        if v > 0.0:
            overshot = True
    assert overshot is True                 # it swung past the rest point
    assert abs(sim.offsets()["HairFront_BS"]) < 0.05   # and settled


def test_vertical_group_follows_the_vertical_axis():
    sim = DragPhysics(_cfg())
    for _ in range(20):
        sim.step(1 / 60.0, 0.0, 3000.0)
    off = sim.offsets()
    assert off["HairPinytail_SY1"] < 0.0   # vertical group reacts to ay
    assert abs(off["HairFront_BS"]) < 1e-9  # horizontal group does not


def test_offsets_are_clamped_to_max_offset():
    sim = DragPhysics(_cfg(max_offset=1.5))
    for _ in range(120):
        sim.step(1 / 60.0, 6000.0, 0.0)
    assert -1.5 <= sim.offsets()["HairFront_BS"] <= 1.5


def test_acceleration_is_clamped_and_smoothing_limits_the_kick():
    sim = DragPhysics(_cfg(accel_smoothing=0.1))
    sim.step(1 / 60.0, 1e9, 0.0)            # absurd input
    assert abs(sim.offsets()["HairFront_BS"]) < 1.0


def test_reset_clears_the_swing():
    sim = DragPhysics(_cfg())
    for _ in range(20):
        sim.step(1 / 60.0, 3000.0, 0.0)
    assert sim.offsets()["HairFront_BS"] != 0.0
    sim.reset()
    assert all(abs(v) < 1e-12 for v in sim.offsets().values())


def test_sign_flip_reverses_the_swing_direction():
    a = DragPhysics(_cfg(sign=-1.0))
    b = DragPhysics(_cfg(sign=1.0))
    for _ in range(12):
        a.step(1 / 60.0, 3000.0, 0.0)
        b.step(1 / 60.0, 3000.0, 0.0)
    assert a.offsets()["HairFront_BS"] < 0.0
    assert b.offsets()["HairFront_BS"] > 0.0


# ── the "too fast" guard ─────────────────────────────────────────────────────

def test_shipped_response_is_slow_and_soft():
    """Regression guard: the shipped config must NOT twitch.  The swing has to
    take a good fraction of a second to build up (soft spring), the offset
    must stay inside max_offset, and the release must settle smoothly."""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    sim = DragPhysics(cfg)
    dt = 1 / 60.0
    peak_t = 0.0
    peak = 0.0
    t = 0.0
    for _ in range(120):                 # 2 s of hard acceleration
        t += dt
        sim.step(dt, 2500.0, 0.0)
        v = abs(sim.offsets()["HairFront_BS"])
        if v > peak:
            peak = v
            peak_t = t
    assert peak <= cfg.max_offset + 1e-9      # never exceeds the clamp
    assert peak_t > 0.2                        # it does NOT snap instantly
    assert cfg.stiffness <= 25.0               # and the spring stays soft
    # a softer spring must settle a lot slower after a brief shove
    fast = DragPhysics(_cfg(stiffness=200.0, damping=6.0, max_offset=4.0))
    slow = DragPhysics(_cfg(stiffness=14.0, damping=5.0, max_offset=4.0))
    dt = 1 / 60.0
    t_fast = t_slow = None
    for i in range(600):                        # 10 s
        drive = 2500.0 if i < 3 else 0.0        # a short shove, then rest
        fast.step(dt, drive, 0.0)
        slow.step(dt, drive, 0.0)
        if t_fast is None and abs(fast.offsets()["HairFront_BS"]) < 0.05:
            t_fast = (i + 1) * dt
        if t_slow is None and abs(slow.offsets()["HairFront_BS"]) < 0.05:
            t_slow = (i + 1) * dt
    assert t_fast is not None and t_slow is not None
    assert t_slow > 1.5 * t_fast                # the soft one lingers


def test_gain_scale_scales_every_group(hot_cfg):
    hot_cfg.write_text(
        '{"gain_scale": 0.5, "groups": {"a": {"axis": "x", "gain": 1.0,'
        ' "params": ["HairFront_BS"]}}}', encoding="utf-8")
    cfg = load_config(hot_cfg)
    assert cfg.gain_scale == 0.5
    assert cfg.params[0].gain == pytest.approx(0.5)


# ── wiring tripwires ─────────────────────────────────────────────────────────

def test_widget_wiring_tripwires():
    root = Path(__file__).parent.parent
    face = (root / "src" / "live2d_face.py").read_text(encoding="utf-8",
                                                      errors="ignore")
    assert "from core.drag_physics import" in face
    assert "DragPhysics(" in face
    # the overlay must be applied AFTER the engine's Update()
    upd = face.index("self._model.Update()")
    apply = face.index("_apply_drag_physics(")
    assert apply > upd
    # additive write: immune to the runtime's replace-vs-blend semantics
    assert "AddParamById" in face
    # and it must never touch the rig's INPUT parameters
    cfg = (root / "config" / "drag_physics.json").read_text(encoding="utf-8")
    assert "ParamFaceAngle" not in cfg
    assert "ParamChestAngle" not in cfg
    assert "ParamBodyAngle" not in cfg
