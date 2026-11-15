"""Feature 19: interruption — the ways you can cut her off, with manners.

What already existed
────────────────────
* barge-in by VOICE (InterruptMonitor stops her mid-reply), and
* sending a typed message stops her voice immediately.

What this adds
──────────────
* a RECOVERY line: when you interrupt her while she was actually mid-reply,
  she yields the floor ("yes? go ahead") instead of just stopping dead —
  rate-limited (min gap, at most N in a row, min reply length) so repeated
  interrupts never become a nagging loop, with a stale streak auto-reset.
* a KEYBOARD shush hotkey (default ctrl+alt+x) that stops her voice mid-
  sentence — for when the mic isn't the reason to stop her.
* TYPING as a barge-in: a typed message trips the interrupt monitor and
  waits a moment for the current turn to end instead of refusing outright.

Design rules: pure module, no Qt/LLM; ``enabled: false`` removes every
addition while keeping the original stop-behaviours.
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "interruption.yaml"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    # What she says after you cut her off while she was mid-reply.
    "recovery_lines": [
        "Hm? Go ahead, I'm listening.",
        "Ah — sorry, you first.",
        "...Yes?",
    ],
    # Only yield when she really was saying something (not a false tap).
    "recovery_min_reply_chars": 24,
    "recovery_min_gap_s": 20.0,
    "recovery_max_streak": 2,     # then silence until the streak goes stale
    "recovery_stale_s": 600.0,    # ...this long without interruptions
    "yield_mood": "curious",      # face shift when she hands you the floor
    "hotkey": "ctrl+alt+x",       # keyboard shush ("" disables it)
}

_cfg_cache: Optional[Dict[str, Any]] = None


def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = dict(DEFAULTS)
    try:
        raw = _safe_load()
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        if "enabled" in raw:
            cfg["enabled"] = bool(raw["enabled"])
        if "recovery_lines" in raw and isinstance(raw["recovery_lines"], list) \
                and raw["recovery_lines"]:
            cfg["recovery_lines"] = [str(x) for x in raw["recovery_lines"]]
        for key in ("recovery_min_reply_chars", "recovery_min_gap_s",
                    "recovery_max_streak", "recovery_stale_s"):
            if key in raw:
                try:
                    cfg[key] = float(raw[key])
                except Exception:
                    pass
        if "yield_mood" in raw:
            cfg["yield_mood"] = str(raw["yield_mood"])
        if "hotkey" in raw:
            cfg["hotkey"] = str(raw["hotkey"])
    _cfg_cache = cfg
    return cfg


def _safe_load():
    import yaml
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


class RecoveryGate:
    """Decides whether she yields the floor after being interrupted.

    Rules (in order):
      * config off / too-short spoken text  -> no;
      * more than ``recovery_max_streak`` back-to-back yields -> no,
        until ``recovery_stale_s`` passes without interrupting her;
      * inside ``recovery_min_gap_s`` of the last yield -> no;
      * otherwise -> yes, with a line drawn from the configured pool.
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None,
                 rng: Optional[random.Random] = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self.reset()

    def reset(self) -> None:
        self._last_yield: Optional[float] = None
        self._streak = 0

    def allow(self, now: datetime, spoken_chars: int = 0,
              cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """The yield line, or None when she should just stop silently."""
        cfg = cfg if cfg is not None else get_config()
        if not cfg.get("enabled", True):
            return None
        if spoken_chars < int(cfg.get("recovery_min_reply_chars", 24)):
            return None
        gap = float(cfg.get("recovery_min_gap_s", 20.0))
        stale = float(cfg.get("recovery_stale_s", 600.0))
        ts = now.timestamp()
        if self._last_yield is not None:
            since = ts - self._last_yield
            if since >= stale:
                self._streak = 0        # long neglect: the slate is wiped
        if self._streak >= int(cfg.get("recovery_max_streak", 2)):
            return None
        if self._last_yield is not None and (ts - self._last_yield) < gap:
            return None
        lines = cfg.get("recovery_lines") or []
        if not lines:
            return None
        self._last_yield = ts
        self._streak += 1
        return str(self._rng.choice(lines))

    @property
    def streak(self) -> int:
        return self._streak


__all__ = [
    "CONFIG_PATH", "DEFAULTS", "RecoveryGate",
    "get_config", "reload_config",
]
