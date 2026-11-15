"""Feature 7: sound scripts.

Python port of the original Risu-style intimacy hook, adapted for this app:

* the original prepends the note to ``scenario`` and appends a suffix to
  ``personality`` and then sets an ``_intimacyNoteInjected`` flag that never
  clears.  Here we keep a small in-memory state with a **linger window**, so the
  note is present for the intimate stretch and then quietly expires, and a
  boundary (Feature 6) clears it immediately.
* matching is regex, case-insensitive, from ``config/sound_scripts.json``.
* trigger words are split into *hard* (enough on their own) and *soft*
  (ambiguous words like ``kiss``/``wet``/``on the bed`` that only count when
  ``soft_threshold`` of them appear together), which removes most of the
  original's false positives.
* the note itself is rewritten for a voice-first app: no narration, no
  asterisks, symbols are permitted but are stripped from the spoken text by
  ``core.audio.clean_for_speech``.

Pure stdlib.  No Qt, no LLM, no network, no disk writes.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "sound_scripts.json"

DEFAULT_LINGER_TURNS = 4
DEFAULT_SOFT_THRESHOLD = 2


@dataclass(frozen=True)
class SoundConfig:
    enabled: bool = False
    linger_turns: int = DEFAULT_LINGER_TURNS
    soft_threshold: int = DEFAULT_SOFT_THRESHOLD
    trigger_patterns: Tuple[Pattern[str], ...] = ()
    context_patterns: Tuple[Pattern[str], ...] = ()
    system_note: str = ""
    state_line: str = ""


@dataclass
class SoundState:
    """How many more turns the sound note stays injected for."""
    turns_left: int = 0

    @property
    def active(self) -> bool:
        return self.turns_left > 0

    def arm(self, turns: int) -> None:
        self.turns_left = max(1, int(turns))

    def clear(self) -> None:
        self.turns_left = 0

    def tick(self) -> None:
        if self.turns_left > 0:
            self.turns_left -= 1


def _compile(patterns) -> Tuple[Pattern[str], ...]:
    out: List[Pattern[str]] = []
    for raw in patterns or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            out.append(re.compile(raw, re.IGNORECASE))
        except re.error as e:
            logger.warning("sound_scripts: bad pattern %r (%s)", raw, e)
    return tuple(out)


def load_config(path: Optional[Path] = None) -> SoundConfig:
    """Load ``config/sound_scripts.json``.  A bad/missing file disables it."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        logger.info("sound_scripts.json not found; sound scripts disabled")
        return SoundConfig()
    except Exception as e:
        logger.warning("sound_scripts.json unreadable; sound scripts disabled: %s", e)
        return SoundConfig()

    note = str(raw.get("system_note") or "").strip()
    if not note:
        logger.warning("sound_scripts.json has no system_note; sound scripts disabled")
        return SoundConfig()

    try:
        linger = max(1, int(raw.get("linger_turns", DEFAULT_LINGER_TURNS)))
    except (TypeError, ValueError):
        linger = DEFAULT_LINGER_TURNS
    try:
        threshold = max(1, int(raw.get("soft_threshold", DEFAULT_SOFT_THRESHOLD)))
    except (TypeError, ValueError):
        threshold = DEFAULT_SOFT_THRESHOLD

    return SoundConfig(
        enabled=bool(raw.get("enabled", False)),
        linger_turns=linger,
        soft_threshold=threshold,
        trigger_patterns=_compile(raw.get("trigger_patterns")),
        context_patterns=_compile(raw.get("context_patterns")),
        system_note=note,
        state_line=str(raw.get("state_line") or "").strip(),
    )


# ── config cache (hot-reloads when the file changes) ──────────────────────────

_cached: Optional[SoundConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_config(path: Optional[Path] = None) -> SoundConfig:
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


# ── detection ─────────────────────────────────────────────────────────────────

def intimacy_triggered(text: Optional[str], cfg: Optional[SoundConfig] = None) -> Optional[str]:
    """Return a short reason when ``text`` reads as an intimate scene.

    One hard pattern is enough; otherwise ``soft_threshold`` distinct soft
    patterns are required.  Returns ``None`` when nothing matched (including
    when the feature is disabled).
    """
    cfg = cfg if cfg is not None else get_config()
    if not cfg.enabled:
        return None
    t = text or ""
    if not t.strip():
        return None

    for pat in cfg.trigger_patterns:
        if pat.search(t):
            return pat.pattern

    soft = [p.pattern for p in cfg.context_patterns if p.search(t)]
    if len(soft) >= cfg.soft_threshold:
        return "soft: " + ", ".join(soft[:3])
    return None


def build_sound_block(state: Optional[SoundState], cfg: Optional[SoundConfig] = None) -> str:
    """Return the injected note (``\"\"`` when inactive/disabled).

    Does **not** consume a turn — call ``state.tick()`` after appending so the
    call site controls the window.
    """
    cfg = cfg if cfg is not None else get_config()
    if not cfg.enabled or state is None or not state.active:
        return ""
    parts = [cfg.system_note]
    if cfg.state_line:
        parts.append(cfg.state_line)
    return "\n".join(parts)


def update_state(state: SoundState, query: Optional[str], boundary: bool,
                 cfg: Optional[SoundConfig] = None) -> str:
    """Arm / clear the window for this turn and return the prompt block.

    * ``boundary`` (a Feature 6 match) always clears and suppresses the block.
    * an intimate ``query`` re-arms the window.
    * otherwise the block is served while the window still has turns left.
    """
    cfg = cfg if cfg is not None else get_config()
    if not cfg.enabled:
        state.clear()
        return ""
    if boundary:
        state.clear()
        return ""
    hit = intimacy_triggered(query, cfg)
    if hit:
        state.arm(cfg.linger_turns)
        logger.debug("[sound] intimate scene armed by %s", hit)
    block = build_sound_block(state, cfg)
    if block:
        state.tick()
    return block


__all__ = [
    "SoundConfig", "SoundState",
    "load_config", "get_config", "reload_config",
    "intimacy_triggered", "build_sound_block", "update_state",
    "DEFAULT_CONFIG_PATH",
]
