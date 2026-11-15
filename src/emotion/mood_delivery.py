"""Feature 8: mood-driven delivery.

The reply mood already reaches the face (``live2d_moods.json``) and the
expression layer (Feature 3), but until now it never reached the voice or the
body.  This module turns the parsed mood into three things:

* **prosody** — a per-mood speed multiplier.  ``core.audio.Speaker`` asks the
  app for a ``mood -> multiplier`` table and applies it on top of the base
  ``voice_speed`` for each sentence (clamped to the existing 0.5..2.0 range),
  so a mid-reply mood change slows only that stretch.
* **interjections** — a short spoken lead-in ("Ha!", "Nn...!") prepended to the
  FIRST spoken frame of a reply, gated by a cross-reply cooldown and a chance.
  Injected at speak time only: the bubble and the audio hear it, while history
  / memory keep the model's original text.
* **gesture fallback** — when the reply itself emitted no gesture/wink tag, one
  configured gesture (``nod``/``shake``/``tilt`` from
  ``config/body_actions.json``) is appended, at most one per reply and never
  past the parser's own tag budget.

All of it is data-driven from ``config/mood_delivery.json`` (mtime hot-reload)
and disappears entirely behind ``enabled: false`` — the provider then returns an
empty table and nothing is injected.

Pure stdlib + JSON.  No Qt, no LLM, no network, no disk writes.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "mood_delivery.json"

DEFAULT_COOLDOWN_TURNS = 3
DEFAULT_CHANCE = 1.0
SPEED_MIN, SPEED_MAX = 0.5, 2.0

# The canonical moods (config/body_actions.json + config/emotions.yaml).  Not
# enforced: extra keys are kept so a custom mood works, these are just the ones
# the shipped table defines.
CANONICAL_MOODS: Tuple[str, ...] = (
    "joyful", "excited", "embarrassed", "sad", "angry",
    "surprised", "curious", "relaxed", "love", "aroused",
)


# ── data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MoodEntry:
    speed: float = 1.0
    interjections: Tuple[str, ...] = ()
    gesture: Optional[str] = None


@dataclass(frozen=True)
class DeliveryConfig:
    enabled: bool = False
    cooldown_turns: int = DEFAULT_COOLDOWN_TURNS
    chance: float = DEFAULT_CHANCE
    moods: Dict[str, MoodEntry] = field(default_factory=dict)

    def speed_table(self) -> Dict[str, float]:
        """``mood -> multiplier`` for the Speaker (``{}`` when disabled)."""
        if not self.enabled:
            return {}
        return {m: e.speed for m, e in self.moods.items()}


def _valid_gestures() -> set:
    """Enabled gesture names from body_actions.json ('wink' excluded: it is
    resolved to its own Tag kind and needs a side)."""
    try:
        from body_tags import actions
        pool = actions().gestures
        names = {k for k, v in pool.items()
                 if isinstance(v, dict) and v.get("enabled", True)}
        return names - {"wink"}
    except Exception as e:                       # pragma: no cover - defensive
        logger.warning("mood_delivery: gestures unavailable (%s)", e)
        return set()


def _clamp_mult(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(SPEED_MIN, min(SPEED_MAX, v))


def _as_interjections(raw) -> Tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def load_config(path: Optional[Path] = None) -> DeliveryConfig:
    """Load ``config/mood_delivery.json``.  A bad/missing file disables it."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        logger.info("mood_delivery.json not found; delivery disabled")
        return DeliveryConfig()
    except Exception as e:
        logger.warning("mood_delivery.json unreadable; delivery disabled: %s", e)
        return DeliveryConfig()

    moods_raw = raw.get("moods")
    if not isinstance(moods_raw, dict) or not moods_raw:
        logger.warning("mood_delivery.json has no moods; delivery disabled")
        return DeliveryConfig()

    valid = _valid_gestures()
    moods: Dict[str, MoodEntry] = {}
    for key, val in moods_raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        name = key.strip().lower()
        if not isinstance(val, dict):
            logger.warning("mood_delivery: entry %r is not an object; skipped", key)
            continue
        gesture = val.get("gesture")
        if isinstance(gesture, str) and gesture.strip():
            gesture = gesture.strip().lower()
            if gesture not in valid:
                logger.warning(
                    "mood_delivery: gesture %r for mood %r is not an enabled "
                    "gesture in body_actions.json; ignored", gesture, name)
                gesture = None
        else:
            gesture = None
        moods[name] = MoodEntry(
            speed=_clamp_mult(val.get("speed", 1.0)),
            interjections=_as_interjections(val.get("interjections")),
            gesture=gesture,
        )

    try:
        cooldown = max(0, int(raw.get("cooldown_turns", DEFAULT_COOLDOWN_TURNS)))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN_TURNS
    try:
        chance = float(raw.get("chance", DEFAULT_CHANCE))
    except (TypeError, ValueError):
        chance = DEFAULT_CHANCE

    return DeliveryConfig(
        enabled=bool(raw.get("enabled", False)),
        cooldown_turns=cooldown,
        chance=max(0.0, min(1.0, chance)),
        moods=moods,
    )


# ── config cache (hot-reloads when the file changes) ──────────────────────────

_cached: Optional[DeliveryConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_config(path: Optional[Path] = None) -> DeliveryConfig:
    global _cached, _cached_path, _cached_mtime
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _cached is not None and p == _cached_path and mtime == _cached_mtime:
        return _cached
    _cached = load_config(p)
    _cached_path = p
    _cached_mtime = mtime
    return _cached


def reload_config() -> None:
    global _cached, _cached_path, _cached_mtime
    _cached = None
    _cached_path = None
    _cached_mtime = -1.0


# ── per-reply state ───────────────────────────────────────────────────────────

class DeliveryState:
    """Cross-reply cooldown + per-reply flags for one conversation stream."""

    __slots__ = ("turns_since_interjection", "interjected", "gestured", "frames")

    def __init__(self) -> None:
        # Large so the FIRST reply is already eligible, whatever the cooldown.
        self.turns_since_interjection = 10 ** 9
        self.interjected = False
        self.gestured = False
        self.frames = 0

    def begin_reply(self) -> None:
        """Called once at the start of each streamed reply."""
        self.interjected = False
        self.gestured = False
        self.frames = 0
        self.turns_since_interjection += 1

    def note_interjection(self) -> None:
        self.interjected = True
        self.turns_since_interjection = 0


# ── the three effects ─────────────────────────────────────────────────────────

def interjection_for(mood: Optional[str], cfg: DeliveryConfig,
                     state: DeliveryState, rng=None) -> str:
    """Return a spoken lead-in for ``mood`` (``""`` when none applies).

    Gates, in order: feature enabled → mood configured → not already used this
    reply → cross-reply cooldown → chance.  A pick resets the cooldown.
    """
    if not cfg.enabled or not mood:
        return ""
    entry = cfg.moods.get(mood)
    if entry is None or not entry.interjections:
        return ""
    if state.interjected:
        return ""
    if state.turns_since_interjection < cfg.cooldown_turns:
        return ""
    roll = rng or random
    if roll.random() >= cfg.chance:
        return ""
    pick = roll.choice(entry.interjections)
    state.note_interjection()
    return pick


def gesture_for(mood: Optional[str], cfg: DeliveryConfig,
                state: DeliveryState) -> Optional[str]:
    """Return the fallback gesture name for ``mood`` (``None`` when none)."""
    if not cfg.enabled or not mood:
        return None
    entry = cfg.moods.get(mood)
    if entry is None or not entry.gesture:
        return None
    if state.gestured:
        return None
    state.gestured = True
    return entry.gesture


def decorate_speech(text: str, mood: Optional[str], tags: Sequence,
                    cfg: DeliveryConfig, state: DeliveryState,
                    reply_kinds: Dict[str, int],
                    cap_remaining: int = 1) -> Tuple[str, list]:
    """Apply the delivery layer to one spoken frame.

    Returns ``(text, tags)`` — the text the voice and bubble should use (the
    original is left untouched by the caller, so history stays clean) and the
    tag list (with an optional synthetic gesture appended).

    * interjection: first frame of the reply only (``state.frames == 1``).
    * gesture: only when the reply emitted no gesture/wink tag so far and the
      parser's per-reply tag budget still has room (``cap_remaining > 0``).
    """
    if not cfg.enabled or not mood:
        return text, tags
    entry = cfg.moods.get(mood)
    if entry is None:
        return text, tags

    state.frames += 1

    has_gesture = (reply_kinds.get("gesture", 0) + reply_kinds.get("wink", 0)) > 0
    if (entry.gesture and not state.gestured and not has_gesture
            and cap_remaining > 0):
        name = gesture_for(mood, cfg, state)
        if name:
            from body_tags import Tag
            tags = list(tags) + [Tag("gesture", name, f"[gesture:{name}]")]

    if state.frames == 1:
        lead = interjection_for(mood, cfg, state)
        if lead:
            text = f"{lead} {text}".strip()

    return text, tags


__all__ = [
    "DeliveryConfig", "DeliveryState", "MoodEntry",
    "load_config", "get_config", "reload_config",
    "interjection_for", "gesture_for", "decorate_speech",
    "CANONICAL_MOODS", "DEFAULT_CONFIG_PATH",
    "SPEED_MIN", "SPEED_MAX",
]
