"""Feature 16: time-awareness — her circadian rhythm.

What it does
────────────
She now knows what WALL-CLOCK time it is, and — more important — what that
time means *for her*: a 2 a.m. Evelyn is tired, short-spoken and wants to
end the day; a 10 a.m. Evelyn is fresh and focused.  That acts in two ways:

* a slow BIAS on the behavior-state axes (valence / arousal / engagement)
  so her *body* moves and postures differently by time of day, and
* a line appended to the system prompt so her *words* also carry it —
  without her announcing the clock unless it is natural.

Design rules (same recipe as always):
* PURE core module: no Qt, no wall-clock reads at import, no guessing —
  every function takes an explicit ``now`` so tests are deterministic.
* Off via config (``enabled: false``) returns to exactly today's behaviour
  (zero bias, empty prompt line).
* Tiers may START > END (wrap past midnight: 23 → 5).

The time-mapping is a *constitution*, not a behavioural script: it is what
a 34-year-old adult who writes for a living naturally does with her day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "circadian.yaml"

_WEEKDAY_TIERS = [
    # start hour is INCLUSIVE, end hour EXCLUSIVE; start > end wraps midnight.
    {"label": "late night", "start": 23, "end": 5,
     "dvalence": -0.15, "darousal": -0.25, "dengagement": -0.10,
     "prompt": ("It's the small hours. You are genuinely worn out: shorter "
                "sentences, slower, softer voice, and you would rather both "
                "of you just wrapped up for the day.")},
    {"label": "dawn", "start": 5, "end": 9,
     "dvalence": 0.05, "darousal": 0.10, "dengagement": 0.10,
     "prompt": ("You're up with the day. Fresh and a little quiet, taking "
                "it slowly with tea while the flat wakes up.")},
    {"label": "daytime", "start": 9, "end": 17,
     "dvalence": 0.05, "darousal": 0.00, "dengagement": 0.05,
     "prompt": ("The working day. Normal daytime energy; deadlines feel "
                "real, and focus isn't hard to come by.")},
    {"label": "evening", "start": 17, "end": 23,
     "dvalence": 0.10, "darousal": -0.05, "dengagement": 0.00,
     "prompt": ("Evening. You wind down; chat is relaxed and unhurried, "
                "the day's pressure is gone.")},
]

_WEEKEND_TIERS = [
    {"label": "weekend late night", "start": 23, "end": 10,
     "dvalence": -0.10, "darousal": -0.20, "dengagement": -0.05,
     "prompt": ("It's late on a weekend and you're still up — sleepy but "
                "unhurried. Mornings-before-ten are not your thing today.")},
    {"label": "weekend morning", "start": 10, "end": 12,
     "dvalence": 0.10, "darousal": 0.05, "dengagement": 0.05,
     "prompt": ("A slow weekend morning, just properly getting going. "
                "Coffee first, agenda never.")},
    {"label": "weekend afternoon", "start": 12, "end": 23,
     "dvalence": 0.10, "darousal": 0.00, "dengagement": 0.00,
     "prompt": ("Weekend afternoon — loose time: hobbies, errands done "
                "or ignored, no sense of a schedule.")},
]

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    # 0 = Monday ... 5 = Saturday, 6 = Sunday
    "weekend_days": [5, 6],
    "note": ("Mention the hour only when it is natural (greetings, goodnight, "
             "deadlines); never announce the time or this line itself."),
    "weekday": _WEEKDAY_TIERS,
    "weekend": _WEEKEND_TIERS,
}

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

def reload_config() -> Dict[str, Any]:
    """Re-read the YAML (hot re-imports pick this up via get_config)."""
    global _cfg_cache
    cfg = dict(DEFAULTS)
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        cfg["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
        wd = raw.get("weekend_days")
        cfg["weekend_days"] = (list(wd) if isinstance(wd, (list, tuple))
                               else list(DEFAULTS["weekend_days"]))
        cfg["note"] = str(raw.get("note", DEFAULTS["note"]))
        for key in ("weekday", "weekend"):
            blocks = raw.get(key)
            cfg[key] = [dict(b) for b in blocks
                        if isinstance(b, dict)] if isinstance(blocks, list) \
                        else [dict(b) for b in DEFAULTS[key]]
    _cfg_cache = cfg
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


# ── tier selection (pure) ────────────────────────────────────────────────────

def _hour_match(tier: Dict[str, Any], hour: int) -> bool:
    """True when ``hour`` (0-23) is inside [start, end); start > end wraps."""
    try:
        s = int(tier.get("start", 0)) % 24
        e = int(tier.get("end", 0)) % 24
    except Exception:
        return False
    if s == e:
        return False                      # empty window => tier never matches
    return (s <= hour < e) if s < e else (hour >= s or hour < e)


def snapshot(now: Optional[datetime] = None,
             cfg: Optional[Dict[str, Any]] = None,
             *, hour: Optional[int] = None,
             weekday: Optional[int] = None) -> Optional["Circadian"]:
    """The circadian state at ``now`` (or None: disabled / no tier covers).

    ``hour`` / ``weekday`` let tests pin a point in time without a clock.
    ``weekday`` follows datetime convention: 0 = Monday ... 6 = Sunday.
    """
    cfg = cfg if cfg is not None else get_config()
    if not cfg.get("enabled", True):
        return None
    if hour is None or weekday is None:
        now = now if now is not None else datetime.now()
        hour = now.hour
        weekday = now.weekday()
    weekend = int(weekday) in [int(d) for d in cfg.get("weekend_days", [])]
    blocks = cfg.get("weekend" if weekend else "weekday") \
             or cfg.get("weekday")       # weekend list may be empty -> reuse
    tier = next((b for b in blocks if _hour_match(b, hour)), None)
    if tier is None:
        return None
    return Circadian(
        label=str(tier.get("label", "")),
        dvalence=float(tier.get("dvalence", 0.0)),
        darousal=float(tier.get("darousal", 0.0)),
        dengagement=float(tier.get("dengagement", 0.0)),
        prompt=str(tier.get("prompt", "")),
        weekend=weekend,
    )


@dataclass(frozen=True)
class Circadian:
    """One time-of-day tier: how she is at this hour."""
    label: str
    dvalence: float
    darousal: float
    dengagement: float
    prompt: str
    weekend: bool

    def bias(self) -> Tuple[float, float, float]:
        """The (dvalence, darousal, dengagement) bias for BehaviorState."""
        return (self.dvalence, self.darousal, self.dengagement)


def bias(e: Optional[Circadian]) -> Tuple[float, float, float]:
    """(0, 0, 0) for None — the disabled / uncovered-hours no-op."""
    return e.bias() if e is not None else (0.0, 0.0, 0.0)


# ── prompt helpers ───────────────────────────────────────────────────────────

_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday", "Sunday")


def prompt_line(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> str:
    """The line appended to the system message ("" when disabled/uncovered)."""
    e = snapshot(now, cfg)
    if e is None:
        return ""
    cfg = cfg if cfg is not None else get_config()
    tm = now.strftime("%H:%M")
    day = _DAYS[now.weekday()] if hasattr(now, "weekday") else ""
    kind = "weekend " if e.weekend else ""
    return (f"Time check: it is {tm} on a {day} ({kind}{e.label} block).\n"
            f"Your rhythm at this hour: {e.prompt}\n{cfg.get('note', '')}")


def describe_time(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Short one-liner for LLM prompts that just need a time hint."""
    e = snapshot(now, cfg)
    day = _DAYS[now.weekday()]
    text = f"It's {now.strftime('%H:%M')} on a {day}"
    if e is not None:
        kind = "weekend" if e.weekend else ""
        label = f"{kind} {e.label}".strip()
        text += f" ({label})"
    return text


__all__ = [
    "CONFIG_PATH", "DEFAULTS", "Circadian",
    "get_config", "reload_config",
    "snapshot", "bias", "prompt_line", "describe_time",
]
