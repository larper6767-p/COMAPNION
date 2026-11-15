"""Feature 13 Stage A: touch-reaction zones + input classification (pure).

No Qt, no OpenGL, no live2d, no clock reads — every decision takes an explicit
timestamp so tests can drive it with a fake clock.

Three pieces:

* ``TouchConfig`` + ``load_config``/``get_config`` — ``config/touch_reactions.json``
  (hot-reloads on file change).  Only ``head`` / ``face`` / ``shoulders`` zones
  are accepted; anything else in the config is dropped with a warning.

  The zone rectangles are **frame-relative** (0..1 of the visible frame, y
  up): a zone is "the top-centre of what you see" and follows the frame
  wherever it is moved or zoomed (Feature 15).  They are converted to model
  units through the frame, so the hit test is exact - no calibration
  constants, no anchor, no zoom_invert.
* the framing transform — :class:`core.framing.Framing` maps model space to
  face-square pixels and back.  The face square's size, the model's zoom and
  the pan are all expressed through the model frame, so the zones follow
  face resize (Ctrl+wheel), model zoom (Alt+wheel) and pan (Alt/Shift+drag)
  automatically and exactly.
* ``TouchClassifier`` — a pure state machine over one pointer press:

    quick click (no Ctrl, moved < cancel_px) ......... talk  (start_turn)
    Ctrl+click on a zone ............................. poke
    press held still on the head zone >= hold_seconds  pat
    press moves > cancel_px in any armed state ....... drag  (window drag)

  ``poll`` drives the pat timer (the app feeds it from a QTimer while armed).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "touch_reactions.json"

# ONLY these zones exist.  Nothing below the chest, nothing else, on purpose.
ALLOWED_ZONES = ("head", "face", "shoulders")

DEFAULTS = {
    "enabled": True,
    "click_action": "talk",     # what a quick zone-less click does
    "hold_seconds": 0.6,        # still-hold on head -> pat
    "cancel_px": 6,             # movement that turns a press into a drag
}

# Frame-relative zone defaults (fractions of the visible frame, y up): the
# head is the top of the frame, the cheeks sit in the middle of the face,
# the shoulders are the lower band.  tools/live2d_preview.py lets you
# re-place them on the model.
DEFAULT_ZONES: Tuple[Tuple[str, Tuple[float, float, float, float]], ...] = (
    ("head",      (0.30, 0.62, 0.40, 0.38)),
    ("face",      (0.38, 0.40, 0.24, 0.26)),
    ("shoulders", (0.12, 0.02, 0.76, 0.26)),
)

DEFAULT_PRIORITY: Tuple[str, ...] = ("face", "head", "shoulders")


@dataclass(frozen=True)
class TouchConfig:
    enabled: bool = bool(DEFAULTS["enabled"])
    click_action: str = str(DEFAULTS["click_action"])
    hold_seconds: float = float(DEFAULTS["hold_seconds"])
    cancel_px: float = float(DEFAULTS["cancel_px"])
    # name -> (x, y, w, h)
    #   zones_relative=True  -> fractions of the frame (0..1, y up): the
    #                          defaults, and what a fresh install uses.
    #   zones_relative=False -> ABSOLUTE model units: the zone is glued to
    #                          her body, so it stays on her head when the
    #                          model is panned or re-framed (what the editor
    #                          writes once you place a zone on the model).
    zones: Tuple[Tuple[str, Tuple[float, float, float, float]], ...] = DEFAULT_ZONES
    zones_relative: bool = True
    # most specific first; overlapping zones resolve to the earlier name
    priority: Tuple[str, ...] = DEFAULT_PRIORITY


def _rects(raw) -> Tuple[Tuple[str, Tuple[float, float, float, float]], ...]:
    if not isinstance(raw, dict):
        return DEFAULT_ZONES
    out = []
    for name in ALLOWED_ZONES:                       # whitelist order = stable
        z = raw.get(name)
        if not isinstance(z, dict):
            continue
        try:
            x, y, w, h = (float(z["x"]), float(z["y"]),
                          float(z["w"]), float(z["h"]))
        except (KeyError, TypeError, ValueError):
            logger.warning("touch_reactions.json: bad zone %r (dropped)", name)
            continue
        if w > 0 and h > 0:
            out.append((name, (x, y, w, h)))
        else:
            logger.warning("touch_reactions.json: zone %r has non-positive "
                           "size (dropped)", name)
    return tuple(out) if out else DEFAULT_ZONES


def load_config(path: Optional[Path] = None) -> TouchConfig:
    """Load the touch config over defaults.  A missing/broken file is the
    default behaviour — a bad config must never break the input stack."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except Exception as e:
        logger.warning("touch_reactions.json unreadable; defaults used: %s", e)
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
        if v != v:                                   # NaN guard
            return default
        return max(lo, min(hi, v))

    click_action = str(raw.get("click_action", DEFAULTS["click_action"]))
    if click_action not in ("talk",):
        click_action = DEFAULTS["click_action"]

    inp = raw.get("input") if isinstance(raw.get("input"), dict) else {}

    def input_num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(inp.get(key, default))
        except (TypeError, ValueError):
            return default
        if v != v:
            return default
        return max(lo, min(hi, v))

    priority = DEFAULT_PRIORITY
    p = raw.get("zone_priority")
    if isinstance(p, list):
        names = tuple(str(n) for n in p if str(n) in ALLOWED_ZONES)
        if names:
            priority = names

    hold = input_num("hold_seconds", float(DEFAULTS["hold_seconds"]), 0.1, 3.0)
    cancel = input_num("cancel_px", float(DEFAULTS["cancel_px"]), 1.0, 50.0)

    if "anchor" in raw or "zoom_invert" in raw:
        # Feature 15: both are obsolete now (the frame is the calibration).
        logger.info("touch_reactions.json: anchor/zoom_invert are obsolete "
                    "(the model frame is the calibration now) - ignored")

    return TouchConfig(
        enabled=bool(raw.get("enabled", DEFAULTS["enabled"])),
        click_action=click_action,
        hold_seconds=hold,
        cancel_px=cancel,
        zones=_rects(raw.get("zones")),
        zones_relative=bool(raw.get("zones_relative", True)),
        priority=priority,
    )


# ── config cache (hot-reload on mtime change) ────────────────────────────────

_cached: Optional[TouchConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_config(path: Optional[Path] = None) -> TouchConfig:
    global _cached, _cached_path, _cached_mtime
    realpath = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = realpath.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _cached is not None and realpath == _cached_path and mtime == _cached_mtime:
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


# ── framing: the model FRAME (model space) <-> face-square pixels ────────────
#
# The frame is the single source of truth (Feature 15): a rectangle in model
# space that the face square shows.  The zone rectangles are stored as
# FRACTIONS of that frame, so they are converted to model units on the fly.

def zone_rect_model(frame: Frame, rect: Tuple[float, float, float, float],
                    relative: bool = True) -> Tuple[float, float, float, float]:
    """Zone rect -> model-space rect.

    ``relative=True``  : the rect is a fraction of the frame (0..1, y up) -
                         the default placement, which follows the frame.
    ``relative=False`` : the rect is already in model units, so the zone is
                         glued to her body.
    """
    x, y, w, h = rect
    if not relative:
        return (x, y, w, h)
    return (frame.x + x * frame.w,
            frame.y + y * frame.h,
            w * frame.w,
            h * frame.h)


def zone_rect_px(framing: Framing, rect: Tuple[float, float, float, float],
                 relative: bool = True) -> Tuple[float, float, float, float]:
    """Zone rect -> (left, top, right, bottom) px."""
    return framing.rect_px(zone_rect_model(framing.frame, rect, relative))


def all_zone_rects_px(cfg: TouchConfig, framing: Framing) -> dict:
    """name -> (left, top, right, bottom) for every zone (overlay drawing)."""
    return {name: zone_rect_px(framing, rect, cfg.zones_relative)
            for name, rect in cfg.zones}


def find_zone(x: float, y: float, cfg: TouchConfig,
              framing: Framing) -> Optional[str]:
    """Which zone contains this face-square-local pixel, most specific first
    (config ``zone_priority``; default face -> head -> shoulders)."""
    for name in cfg.priority:
        rect = dict(cfg.zones).get(name)
        if rect is None:
            continue
        l, t, r, b = zone_rect_px(framing, rect, cfg.zones_relative)
        if l <= x <= r and t <= y <= b:
            return name
    return None


# ── classifier ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Outcome:
    kind: str                     # "talk" | "poke" | "pat" | "pat_end" | "drag"
    zone: Optional[str] = None    # zone resolvable at press time; face-local
    duration: float = 0.0         # for pat_end: full hold duration in seconds


class TouchClassifier:
    """Pure press/move/poll/release state machine for ONE press.

    The app resolves the zone at press time and drives ``poll`` from a timer
    (pats need a still-hold, and a still-hold produces no move events to wake
    the handler).  All coordinates are face-widget-local pixels.

    Every method returns an :class:`Outcome` or ``None`` (meaning "not ours").
    """

    def __init__(self) -> None:
        self._id = _PressData()

    @property
    def armed(self) -> bool:
        return self._id.kind is not None

    def press(self, cfg: TouchConfig, x: float, y: float, t: float,
              zone: Optional[str], ctrl: bool) -> None:
        self._id = _PressData(x=x, y=y, t=t, zone=zone,
                              kind="poke" if ctrl else
                              ("pat" if zone == "head" else "plain"))

    def move(self, cfg: TouchConfig, x: float, y: float,
             t: float) -> Optional[Outcome]:
        if self._id.kind is None:
            return None
        moved = ((x - self._id.x) ** 2 + (y - self._id.y) ** 2) > cfg.cancel_px ** 2
        if not moved:
            return None
        if self._id.fired:
            # A pat that already fired is cut short by the movement: report the
            # hold span for escalation credit instead of becoming a window drag.
            zone, started = self._id.zone, self._id.t
            self._id = _PressData()
            return Outcome("pat_end", zone, duration=max(0.0, t - started))
        self._id = _PressData()
        return Outcome("drag")

    def poll(self, cfg: TouchConfig, t: float) -> Optional[Outcome]:
        """Pat fires only when armed on a pristine still-hold (any move past
        ``cancel_px`` already downgraded the press to a drag)."""
        if self._id.kind != "pat":
            return None
        if t - self._id.t < cfg.hold_seconds:
            return None
        zone = self._id.zone
        self._id.fired = True          # keep armed; release reports the hold span
        return Outcome("pat", zone)

    def release(self, cfg: TouchConfig, x: float, y: float,
                t: float) -> Optional[Outcome]:
        kind = self._id.kind
        zone = self._id.zone
        fired = self._id.fired
        started = self._id.t
        self._id = _PressData()
        if kind == "poke":
            return Outcome("poke", zone)
        if kind in ("pat", "plain"):
            if kind == "pat" and fired:
                # The reaction already fired at the hold boundary; the release
                # only reports the FULL hold so escalation can credit the rest.
                return Outcome("pat_end", zone, duration=max(0.0, t - started))
            # A quick click is unchanged regardless of where it landed (the
            # pet's original face-square + hit-test logic runs as before).
            return Outcome("talk", zone)
        return None


class _PressData:
    """Internal armed-press record (kept mutable, tiny, never exported)."""

    __slots__ = ("x", "y", "t", "zone", "kind", "fired")

    def __init__(self, x: float = 0.0, y: float = 0.0, t: float = 0.0,
                 zone: Optional[str] = None, kind: Optional[str] = None,
                 fired: bool = False):
        self.x, self.y, self.t = x, y, t
        self.zone = zone
        self.kind = kind
        self.fired = fired          # True once poll() fired the pat reaction


__all__ = [
    "TouchConfig", "Outcome", "TouchClassifier",
    "load_config", "get_config", "reload_config",
    "zone_rect_model", "zone_rect_px", "all_zone_rects_px", "find_zone",
    "ALLOWED_ZONES", "DEFAULT_CONFIG_PATH", "DEFAULTS", "DEFAULT_ZONES",
]
