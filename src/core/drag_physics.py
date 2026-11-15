"""Feature 14: drag physics — inertia on the Live2D physics outputs.

Why this exists
---------------
The model ships a full Cubism physics rig (physics3.json: 196 outputs for
hair, fringe, clothes, skirt, ears, chest).  Its INPUTS are model
*parameters* (``ParamFaceAngleX``, ``ParamChestAngleX``,
``ParamBodyAngleZ0``, ``ParamBreath`` ...), never the model's screen
position - so panning her (Alt/Shift+drag) or dragging the pet window does
NOT move the physics at all: the rig never learns that her body moved.

What this module does
---------------------
A tiny, pure spring-damper layer ("inertia overlay") that is driven by the
ACCELERATION of her body on screen.  The SDK's own physics keeps running and
stays the base value; this layer adds a lag/overshoot offset on top of the
soft appendages (hair, fringe, clothes, skirt, ears), which is exactly what a
real body does when you yank it around:

    drag right fast  -> hair swings back (lag), overshoots, settles
    stop abruptly    -> hair swings forward, oscillates, settles back to rest

Pure by construction: no Qt, no GL, no clock reads - the caller supplies
``dt`` and the acceleration.  The widget applies the resulting offsets to the
model AFTER ``model.Update()`` (so they land on top of the engine values)
using the transient ``SetParamById`` path, so the SDK's physics engine keeps
owning those parameters.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "drag_physics.json"

# ── config ───────────────────────────────────────────────────────────────────

DEFAULTS: Dict = {
    "enabled": True,
    # how strongly each kind of movement drives the sim
    "window_drag_gain": 0.8,     # the pet window itself is dragged
    "model_drag_gain": 1.0,      # the model is panned inside the square
    # signal conditioning
    "accel_smoothing": 0.6,      # EMA factor on the measured acceleration
    "max_accel": 800.0,         # px/s^2 clamp (protects against jumps)
    "deadzone_px": 0.75,         # sub-pixel jitter never moves anything
    # spring - deliberately soft and slow: a heavy, lazy sway, not a twitch.
    # The group gains are small on purpose: the spring's equilibrium is
    # drive/k, so a gain that would slam the offset into max_offset within
    # one frame is what makes the hair "twitch" instead of sway.
    "stiffness": 14.0,           # spring constant (1/s^2)
    "damping": 5.0,              # damping (1/s), near-critical -> no ringing
    "max_offset": 4.0,           # per-param clamp (param units)
    "sign": -1.0,                # -1 = lag behind the motion (physical)
    "gain_scale": 0.5,           # global strength multiplier for every group
    # param groups: axis x (side swing) or y (vertical swing)
    "groups": {
        "hair_side": {
            "axis": "x", "gain": 0.008,
            "params": [
                "HairFront_LS", "HairFront_LS2", "HairFront_LS3", "HairFront_LS4",
                "HairFront_BS", "HairFront_BS2", "HairFront_BS3", "HairFront_BS4",
                "HairFront2_LS1", "HairFront2_LS2", "HairFront2_LS3",
                "HairFront2_LS4",
                "HairPinytail_S1", "HairPinytail_S2", "HairPinytail_S3",
                "HairPinytail_S4", "HairPinytail_S5", "HairPinytail_S6",
            ],
        },
        "hair_vert": {
            "axis": "y", "gain": 0.006,
            "params": [
                "HairFront_LSY", "HairFront_LSY2", "HairFront2_LSY1",
                "HairFront2_LSY2", "HairFront_BSY", "HairFront_BSY2",
                "HairFront_BSY3", "HairPinytail_SY1", "HairPinytail_SY2",
                "HairPinytail_SY3", "HairPinytail_SY4",
                "HairFront_IS1", "HairFront_IS2", "HairFront_IS3",
                "HairFront_IS4", "SillyHair_S1", "SillyHair_S2", "SillyHair_S3",
            ],
        },
        "fringe": {
            "axis": "x", "gain": 0.025,
            "params": [
                "HanfFront_RS1", "HanfFront_RS2", "HanfFront_RS3",
                "HanfFront_LS1", "HanfFront_LS2", "HanfFront_LS3",
                "HanfFront_RS4", "HanfFront_RS5", "HanfFront_RS6",
                "HanfFront_LS4", "HanfFront_LS5",
            ],
        },
        "clothes": {
            "axis": "x", "gain": 0.02,
            "params": [
                "ClothesHand_RS1", "ClothesHand_RS2", "ClothesHand_RS3",
                "ClothesHand_LS1", "ClothesHand_LS2", "ClothesHand_LS3",
                "ClothesHand_RSY1", "ClothesHand_RSY2",
                "ClothesHand_LSY1", "ClothesHand_LSY2",
            ],
        },
        "skirt": {
            "axis": "x", "gain": 0.018,
            "params": [
                "BowSkirtBack_S1", "BowSkirtBack_S2", "BowSkirtBack_S3",
                "BowSkirtBack_SY1",
            ],
        },
        "ears": {
            "axis": "x", "gain": 0.013,
            "params": ["Ear_SR1", "Ear_SR2", "Ear_SR3",
                       "Ear_SL1", "Ear_SL2", "Ear_SL3"],
        },
        "body": {
            "axis": "x", "gain": 0.008,
            "params": [
                "Chest_S1", "Chest_S2", "Chest_S3",
                "Close_S1", "Close_S2", "Close_SY1", "Close_SY2",
                "Close2_SY1", "Close2_S1",
            ],
        },
    },
}


@dataclass(frozen=True)
class ParamSpec:
    name: str
    axis: str          # "x" or "y"
    gain: float


@dataclass(frozen=True)
class DragPhysicsConfig:
    enabled: bool = True
    window_drag_gain: float = 1.0
    model_drag_gain: float = 1.2
    accel_smoothing: float = 0.35
    max_accel: float = 6000.0
    deadzone_px: float = 0.75
    stiffness: float = 55.0
    damping: float = 6.0
    max_offset: float = 6.0
    sign: float = -1.0
    gain_scale: float = 1.0        # global strength multiplier (all groups)
    params: Tuple[ParamSpec, ...] = ()


def _num(raw: Dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(raw.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    if v != v:                       # NaN guard
        return float(default)
    return max(lo, min(hi, v))


def load_config(path: Optional[Path] = None) -> DragPhysicsConfig:
    """Read config/drag_physics.json.  Missing/broken file = defaults; a bad
    group never takes the whole config down."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    groups = raw.get("groups")
    if not isinstance(groups, dict) or not groups:
        groups = DEFAULTS["groups"]

    specs: List[ParamSpec] = []
    seen = set()
    gain_scale = _num(raw, "gain_scale", float(DEFAULTS["gain_scale"]), 0.0, 10.0)
    for _gname, g in groups.items():
        if not isinstance(g, dict):
            continue
        axis = "y" if str(g.get("axis", "x")).lower() == "y" else "x"
        try:
            gain = float(g.get("gain", 1.0))
        except (TypeError, ValueError):
            gain = 1.0
        if gain != gain:
            gain = 1.0
        gain *= gain_scale
        names = g.get("params")
        if not isinstance(names, (list, tuple)):
            continue
        for name in names:
            name = str(name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            specs.append(ParamSpec(name, axis, gain))

    return DragPhysicsConfig(
        enabled=bool(raw.get("enabled", DEFAULTS["enabled"])),
        window_drag_gain=_num(raw, "window_drag_gain",
                              float(DEFAULTS["window_drag_gain"]), 0.0, 10.0),
        model_drag_gain=_num(raw, "model_drag_gain",
                              float(DEFAULTS["model_drag_gain"]), 0.0, 10.0),
        accel_smoothing=_num(raw, "accel_smoothing",
                              float(DEFAULTS["accel_smoothing"]), 0.0, 1.0),
        max_accel=_num(raw, "max_accel", float(DEFAULTS["max_accel"]),
                        1.0, 1e6),
        deadzone_px=_num(raw, "deadzone_px", float(DEFAULTS["deadzone_px"]),
                         0.0, 50.0),
        stiffness=_num(raw, "stiffness", float(DEFAULTS["stiffness"]),
                       0.1, 1000.0),
        damping=_num(raw, "damping", float(DEFAULTS["damping"]), 0.0, 200.0),
        max_offset=_num(raw, "max_offset", float(DEFAULTS["max_offset"]),
                        0.0, 100.0),
        sign=_num(raw, "sign", float(DEFAULTS["sign"]), -1.0, 1.0),
        gain_scale=gain_scale,
        params=tuple(specs),
    )


# ── config cache (hot-reload) ────────────────────────────────────────────────

_cached: Optional[DragPhysicsConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_config(path: Optional[Path] = None) -> DragPhysicsConfig:
    global _cached, _cached_path, _cached_mtime
    realpath = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = realpath.stat().st_mtime
    except OSError:
        mtime = -1.0
    if (_cached is not None and realpath == _cached_path
            and mtime == _cached_mtime):
        return _cached
    _cached = load_config(realpath)
    _cached_path = realpath
    _cached_mtime = mtime
    return _cached


def reload_config() -> None:
    global _cached, _cached_path, _cached_mtime
    _cached = None
    _cached_path = None
    _cached_mtime = -1.0


# ── the sim ──────────────────────────────────────────────────────────────────

class DragPhysics:
    """One spring-damper per configured parameter, driven by the acceleration
    of her body.

    ``step(dt, ax, ay)`` takes the body acceleration in px/s^2 (x right,
    y down - the caller flips the sign if it prefers y up) and returns
    ``{param_name: offset}``.  Offsets are param units, added on top of the
    SDK physics value by the widget.
    """

    def __init__(self, cfg: Optional[DragPhysicsConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else get_config()
        # param -> [offset, velocity]
        self._state: Dict[str, List[float]] = {
            s.name: [0.0, 0.0] for s in self.cfg.params}
        self._last_ax = 0.0
        self._last_ay = 0.0
        self._smooth_ax = 0.0
        self._smooth_ay = 0.0

    # ── helpers ────────────────────────────────────────────────────────────
    def reset(self) -> None:
        for v in self._state.values():
            v[0] = 0.0
            v[1] = 0.0
        self._smooth_ax = self._smooth_ay = 0.0
        self._last_ax = self._last_ay = 0.0

    @property
    def active(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.params)

    def offsets(self) -> Dict[str, float]:
        return {name: v[0] for name, v in self._state.items()}

    # ── main step ──────────────────────────────────────────────────────────
    def step(self, dt: float, ax: float, ay: float) -> Dict[str, float]:
        """Advance the sim.  Returns the per-param offsets to add on top of
        the SDK physics values (empty dict when the feature is off)."""
        if not self.active or dt <= 0.0:
            return {}

        # clamp + smooth the incoming acceleration
        max_a = self.cfg.max_accel
        ax = max(-max_a, min(max_a, float(ax)))
        ay = max(-max_a, min(max_a, float(ay)))
        s = self.cfg.accel_smoothing
        self._smooth_ax += (ax - self._smooth_ax) * s
        self._smooth_ay += (ay - self._smooth_ay) * s

        k = self.cfg.stiffness
        c = self.cfg.damping
        mo = self.cfg.max_offset
        sign = self.cfg.sign

        for spec in self.cfg.params:
            st = self._state[spec.name]
            x, v = st[0], st[1]
            drive = sign * spec.gain * (self._smooth_ax if spec.axis == "x"
                                        else self._smooth_ay)
            a = -k * x - c * v + drive
            v += a * dt
            x += v * dt
            if x > mo:
                x = mo
                v = 0.0
            elif x < -mo:
                x = -mo
                v = 0.0
            st[0] = x
            st[1] = v

        self._last_ax = ax
        self._last_ay = ay
        return self.offsets()


__all__ = [
    "DragPhysics", "DragPhysicsConfig", "ParamSpec",
    "load_config", "get_config", "reload_config",
    "DEFAULTS", "DEFAULT_CONFIG_PATH",
]
