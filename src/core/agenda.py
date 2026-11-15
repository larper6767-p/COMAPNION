"""Feature 17: her own life — a simulated daily agenda.

What it does
────────────
Evelyn is a luxury real estate agent with showings, staging and an evening
wind-down.  When you talk to her mid-block, her words know it ("you've
caught me between showings"), and when she starts a conversation herself
she does it the way a busy person does — with a small excuse about what
she was up to.

* PURE core module: no GUI, no wall-clock reads at import; every function
  takes an explicit ``now`` so tests are deterministic.
* Blocks wrap past midnight when ``end`` <= ``start`` (all times are
  ``HH:MM`` local).
* ``enabled: false`` returns exactly today's behaviour.

Feeding points (wired in app.py):
* a line about the current activity in the system prompt (System message),
* the proactive chooser: occasionally, INSTEAD of a lonely-pool line, she
  opens with what she was in the middle of (rate-limited: at most one
  excuse per block, and only when a work-like block is actually running).
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "agenda.yaml"


def _block(label: str, start: str, end: str, kind: str = "activity",
           prompt: str = "") -> Dict[str, Any]:
    return {"label": label, "start": start, "end": end, "kind": kind,
            "prompt": prompt}


_WEEKDAY = [
    _block("morning: espresso, skincare, the day's calls", "05:40", "08:00",
           "chore", "You are up before the city: espresso, skincare, hair, "
           "the day's calls already in your head."),
    _block("calls and early showings", "08:00", "10:30",
           "work", "You are in work mode: calls, keys, a showing that has to "
           "start on time. Polite, precise, already assessing the room."),
    _block("listing work: showings, staging, client calls", "10:30", "12:45",
           "work", "This is the block you protect: listings, staging, clients "
           "who want the impossible. Interruptions get a look, then you drag "
           "yourself back."),
    _block("lunch you mostly skip + one call", "13:00", "14:00",
           "meal", "It's lunch and there's one call you keep postponing."),
    _block("afternoon showings and paperwork", "15:00", "18:00",
           "work", "Showings, paperwork, the Monday numbers if it's Monday. "
           "Focused, a little clipped, espresso rescues happening."),
    _block("evening wind-down: wine, reading, no phone", "20:00", "22:30",
           "free", "You have put the day away: one glass of white wine, the "
           "throw, a book. No performance."),
]

_WEEKEND = [
    _block("a slow weekend wake-up", "09:30", "11:30",
           "free", "Weekend mornings don't start on an alarm if you can "
           "avoid it."),
    _block("farmers market and errands", "12:00", "13:30",
           "errand", "The farmers market and the errands you keep postponing "
           "— out of the apartment, in and out of shops."),
    _block("gallery opening you leave after twenty minutes", "18:00", "20:00",
           "free", "A gallery opening you'll leave after twenty minutes; the "
           "point is being seen, not the art."),
]

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "free_prompt": ("No fixed plan right now — you drift: tidying, a gallery "
                    "you might not go to, the long way home."),
    "kinds_busy": ["work", "chore", "errand"],   # which blocks count as busy
    "busy_excuse_chance": 0.5,                   # per eligible proactive fire
    "max_excuses_per_block": 1,                  # then she has said it once
    "min_excuse_gap_s": 180.0,                   # and never twice in a row
    "busy_lines": [
        "Oh — give me a second, I'm right in the middle of {activity}.",
        "Sorry, {activity} has me in its grip. What's up?",
        "Hmm? One sec... {activity} brain. Okay, go.",
    ],
    "weekday": list(_WEEKDAY),
    "weekend": list(_WEEKEND),
}

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = dict(DEFAULTS)
    cfg["weekday"] = [dict(b) for b in DEFAULTS["weekday"]]
    cfg["weekend"] = [dict(b) for b in DEFAULTS["weekend"]]
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        cfg["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
        for key, cast in (("free_prompt", str),
                          ("kinds_busy", list),
                          ("busy_excuse_chance", float),
                          ("max_excuses_per_block", int),
                          ("min_excuse_gap_s", float),
                          ("busy_lines", list)):
            if key in raw:
                try:
                    cfg[key] = cast(raw[key])
                except Exception:
                    pass
        for key in ("weekday", "weekend"):
            blocks = raw.get(key)
            if isinstance(blocks, list):
                cfg[key] = [dict(b) for b in blocks if isinstance(b, dict)]
    _cfg_cache = cfg
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


# ── block maths (pure) ───────────────────────────────────────────────────────

def parse_hhmm(s: str) -> Optional[int]:
    """'09:30' -> 570 minutes of day; None when unparsable."""
    try:
        if not isinstance(s, str) or ":" not in s:
            return None
        h, m = s.split(":", 1)
        hh, mm = int(h), int(m)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh * 60 + mm
    except Exception:
        return None


class Block:
    """One agenda block with start/end as minutes-of-day; wraps past midnight."""

    def __init__(self, label: str, start_min: int, end_min: int,
                 kind: str, prompt: str) -> None:
        self.label = label
        self.start = int(start_min) % 1440
        self.end = int(end_min) % 1440
        self.kind = kind
        self.prompt = prompt

    def in_window(self, minute: int) -> bool:
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end


def _blocks_for(cfg: Dict[str, Any], now: datetime) -> List[Block]:
    weekend = now.weekday() >= 5
    daily = cfg.get("weekend" if weekend else "weekday") or cfg.get("weekday")
    out: List[Block] = []
    for b in daily:
        s = parse_hhmm(b.get("start", ""))
        e = parse_hhmm(b.get("end", ""))
        if s is None or e is None:
            continue
        out.append(Block(str(b.get("label", "")), s, e,
                         str(b.get("kind", "activity")),
                         str(b.get("prompt", ""))))
    return out


def current_block(now: datetime,
                  cfg: Optional[Dict[str, Any]] = None) -> Optional[Block]:
    """The block running at ``now`` (None = her day is open / free time)."""
    cfg = cfg if cfg is not None else get_config()
    if not cfg.get("enabled", True):
        return None
    minute = now.hour * 60 + getattr(now, "minute", 0)
    for b in _blocks_for(cfg, now):
        if b.in_window(minute):
            return b
    return None


def minutes_until_next_transition(now: datetime,
                                  cfg: Optional[Dict[str, Any]] = None,
                                  ) -> Optional[int]:
    """Minutes until the next block boundary (start or end), or None."""
    cfg = cfg if cfg is not None else get_config()
    minute = now.hour * 60 + getattr(now, "minute", 0)
    edges = set()
    for b in _blocks_for(cfg, now):
        if b.start != 0:                # midnight starts the free day
            edges.add(b.start)
        if b.end != 0:
            edges.add(b.end)
    if not edges:
        return None
    deltas = [((e - minute) % 1440) for e in edges if (e - minute) % 1440]
    return min(deltas) if deltas else None


# ── prompt helpers ───────────────────────────────────────────────────────────

def prompt_line(now: datetime, cfg: Optional[Dict[str, Any]] = None) -> str:
    """The agenda section of the system message ("" when disabled)."""
    cfg = cfg if cfg is not None else get_config()
    if not cfg.get("enabled", True):
        return ""
    b = current_block(now, cfg)
    nxt = minutes_until_next_transition(now, cfg)
    if b is not None:
        until = f" (about {nxt} min left)" if nxt is not None else ""
        text = f"Your agenda: you're in the middle of \"{b.label}\"{until}.\n{b.prompt}"
    else:
        text = f"Your agenda: {cfg.get('free_prompt', '')}"
        if nxt is not None:
            text += f"\nNext up in about {nxt} minutes."
    return text


# ── the proactive excuse watch (pure, stateful) ──────────────────────────────

class AgendaWatch:
    """Hands out 'what I'm in the middle of' openers for proactive messages.

    Rules: only inside a busy-kind block, at most once per block, never
    twice within ``min_excuse_gap_s``, and drawn with the configured
    chance.  Returns None otherwise — the normal tone pools then speak.
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None,
                 rng: Optional[random.Random] = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._block_label: Optional[str] = None
        self._used: int = 0
        self._last_excuse: Optional[float] = None   # wall clock seconds

    def proactive_line(self, now: datetime,
                       cfg: Optional[Dict[str, Any]] = None,
                       ) -> Optional[str]:
        cfg = cfg if cfg is not None else get_config()
        if not cfg.get("enabled", True):
            return None
        block = current_block(now, cfg)
        if block is None:
            self._block_label = None
            self._used = 0
            return None
        if block.kind not in cfg.get("kinds_busy", []):
            return None
        if block.label != self._block_label:
            self._block_label = block.label
            self._used = 0
        max_per = int(cfg.get("max_excuses_per_block", 1))
        if self._used >= max_per:
            return None
        gap = float(cfg.get("min_excuse_gap_s", 180.0))
        if self._last_excuse is not None and \
                (now.timestamp() - self._last_excuse) < gap:
            return None
        chance = float(cfg.get("busy_excuse_chance", 0.5))
        if chance <= 0.0 or self._rng.random() >= chance:
            return None
        lines = cfg.get("busy_lines") or []
        if not lines:
            return None
        self._used += 1
        self._last_excuse = now.timestamp()
        return str(self._rng.choice(lines)).format(activity=block.label)


__all__ = [
    "CONFIG_PATH", "DEFAULTS", "Block",
    "get_config", "reload_config",
    "parse_hhmm", "current_block", "minutes_until_next_transition",
    "prompt_line", "AgendaWatch",
]
