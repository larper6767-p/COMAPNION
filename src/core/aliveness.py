"""Feature 27: feature-owned aliveness layer.

The character card in ``config/personality.json`` is the user's text and is
never edited by this feature.  This module loads optional prompt sections from
``config/aliveness.yaml`` so conversational habits (turn shape, active
listening, in-character fallback, opinions, examples) can be corrected without
touching the card.

The module is pure: it reads one YAML file, never the wall clock, Qt or the
network.  Missing/malformed config means "disabled", so the companion behaves
exactly as before.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "aliveness.yaml"

# Section order matters: the fallback and opinions come after the turn rules,
# and the examples last.  Unknown keys are ignored.
_SECTIONS = ("turn_shape", "listening", "in_character_fallback",
             "narration", "memory_notes", "opinions", "examples")

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "daily_summary": False,
    "restart_history": 0,
    "narration_enabled": True,
    "self_notes": False,
    "max_notes_per_day": 5,
    "proactive_instruction": "",
    "question_guard": "",
    "turn_shape": "",
    "listening": "",
    "in_character_fallback": "",
    "opinions": "",
    "examples": "",
    "closeness_tiers_extra": {},
}

_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_config(path: Optional[Path] = None, force: bool = False) -> Dict[str, Any]:
    """Load and normalize ``config/aliveness.yaml``.

    A missing or malformed file returns the disabled defaults.  The mtime cache
    makes live edits cheap without requiring a restart.
    """
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return dict(DEFAULTS)

    key = str(p)
    cached = _cache.get(key)
    if not force and cached is not None and cached[0] == mtime:
        return dict(cached[1])

    cfg = dict(DEFAULTS)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for name in _SECTIONS + ("proactive_instruction", "question_guard"):
            if name in raw:
                cfg[name] = _as_text(raw[name])
        cfg["enabled"] = bool(raw.get("enabled", False))
        cfg["daily_summary"] = bool(raw.get("daily_summary", False))
        cfg["narration_enabled"] = bool(raw.get("narration_enabled", True))
        cfg["self_notes"] = bool(raw.get("self_notes", False))
        if "max_notes_per_day" in raw:
            try:
                cfg["max_notes_per_day"] = max(
                    0, int(raw.get("max_notes_per_day")) or 0)
            except (TypeError, ValueError):
                cfg["max_notes_per_day"] = 5
        if "restart_history" in raw:
            try:
                cfg["restart_history"] = max(0, int(raw["restart_history"]))
            except (TypeError, ValueError):
                cfg["restart_history"] = 0
        extra = raw.get("closeness_tiers_extra")
        if isinstance(extra, dict):
            clean: Dict[str, str] = {}
            for k, v in extra.items():
                try:
                    score = int(k)
                except (TypeError, ValueError):
                    continue
                text = _as_text(v)
                if 0 <= score <= 200 and text:
                    clean[str(score)] = text
            cfg["closeness_tiers_extra"] = clean

    _cache[key] = (mtime, dict(cfg))
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    return load_config(force=force)


def prompt_block(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The static aliveness prompt, or "" when disabled/unconfigured."""
    c = cfg if cfg is not None else get_config()
    if not c.get("enabled"):
        return ""
    parts = [_as_text(c.get(name)) for name in _SECTIONS]
    return "\n\n".join(p for p in parts if p)


_QUESTION_END = re.compile(r"\?\s*[\"')\]\u2019\u201d]*\s*$")


def ends_with_question(text: str) -> bool:
    return bool(_QUESTION_END.search((text or "").strip()))


def _spoken_text(text: str) -> str:
    """Voice-only text: drop silent narration and private memory notes.

    The guard must judge what she actually said.  ``last_reply`` is the raw
    model frame, so a trailing ``**beat**`` or ``{{remember: ...}}`` used to
    hide a spoken question from :func:`ends_with_question` and silently
    disable the next-turn guard.
    """
    if not isinstance(text, str):
        return ""
    try:
        from core import narration, memory_notes
        return narration.strip_narration(memory_notes.strip_notes(text))
    except Exception:
        return text.strip()


def guard_block(last_reply: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """The one-turn guard appended after the dynamic state line."""
    c = cfg if cfg is not None else get_config()
    if not c.get("enabled"):
        return ""
    if not ends_with_question(_spoken_text(last_reply)):
        return ""
    return _as_text(c.get("question_guard"))


def proactive_instruction(cfg: Optional[Dict[str, Any]] = None) -> str:
    c = cfg if cfg is not None else get_config()
    if not c.get("enabled"):
        return ""
    return _as_text(c.get("proactive_instruction"))


def closeness_tiers_extra(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    c = cfg if cfg is not None else get_config()
    if not c.get("enabled"):
        return {}
    return dict(c.get("closeness_tiers_extra") or {})


# ── restart continuity (Feature 27b) ──────────────────────────────────────────

_TURN_ROLES = ("user", "assistant")


def select_replay(
    messages: Iterable[Any],
    keep: int,
    ceiling: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Pick the tail of an episode log for a launch replay.  Pure.

    Only real turns survive: non-turn roles and empty texts are dropped, the
    result is kept oldest→newest (history order), and ``keep`` is capped by
    ``ceiling`` so a reloaded window can never trip the in-session summariser
    on turn one.
    """
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        return []
    if keep <= 0:
        return []
    if ceiling is not None:
        try:
            keep = min(keep, max(0, int(ceiling)))
        except (TypeError, ValueError):
            pass

    if isinstance(messages, (list, tuple)):
        tail = list(messages)[-keep:]
    else:
        return []

    out: List[Dict[str, str]] = []
    for m in tail:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in _TURN_ROLES:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content.strip()})
    return out


def last_exchange_pair(messages: List[Any]) -> Optional[Tuple[str, str]]:
    """The most recent user→assistant pair in a replay (or None).  Pure."""
    for i in range(len(messages) - 1, 0, -1):
        newer = messages[i]
        older = messages[i - 1]
        if (isinstance(newer, dict) and newer.get("role") == "assistant"
                and isinstance(older, dict) and older.get("role") == "user"):
            newer_text = newer.get("content")
            older_text = older.get("content")
            if isinstance(newer_text, str) and isinstance(older_text, str):
                return (older_text, newer_text)
    return None


__all__ = [
    "CONFIG_PATH", "DEFAULTS", "load_config", "get_config", "prompt_block",
    "ends_with_question", "guard_block", "proactive_instruction",
    "closeness_tiers_extra", "select_replay", "last_exchange_pair",
]
