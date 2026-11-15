"""Feature 24: long mode — she unpacks when you actually ask.

The persona pins her replies to two-to-four short sentences, which is the
right DEFAULT for a voice companion (nobody wants uninvited lectures).
Sometimes you really do want the full unpacking.  This module is the
small, pure brain for that:

* ``parse_slash()`` — the ``/long`` / ``/short`` toggles (sticky for the
  session until the other one is typed).
* ``phrase_hit()`` — ONE-turn stretch triggers ("tell me everything",
  "go on", "keep going"); they stretch a single turn only, never set the
  sticky mode.
* ``long_suffix()`` — the override paragraph appended LAST to the system
  message so it beats the earlier length rules; it also cancels the
  question-stacking habit.

PURE core module: no LLM, no Qt, no wall-clock.  The wiring lives in
she.py (system_message + _verb_command + the console slash chain) and
app.py (one dispatch call in _respond before the LLM turn).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "verbosity.yaml"

DEFAULTS: Dict[str, Any] = {
    "enabled": False,            # off by default: changes what she'll say
    "long_command": "/long",
    "short_command": "/short",
    "phrases": [
        "tell me everything",
        "go on",
        "keep going",
        "don't stop",
        "the long story",
        "the whole story",
        "unpack it",
        "go long",
        "walk me through it",
        "with all the details",
    ],
    "suffix": (
        "SUSPENSION AND OVERRIDE (this turn only): ignore every earlier "
        "reply-length rule (\"two to four short sentences\", \"under 500 "
        "characters\", any character cap). For this message, talk long and "
        "unpacked - 400 to 1200 words as the thought needs, letting the "
        "story breathe. Keep the spoken-aloud style and the mood/tag system "
        "exactly as directed elsewhere. Do not stack questions: at most one "
        "short question at the very end, or land on a statement."
    ),
    "feedback_on": "Long mode on. Big questions get the full unpacking now.",
    "feedback_off": "Normal chatter for me.",
}

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

CONFIG_TEXT_KEYS = ("long_command", "short_command", "suffix",
                    "feedback_on", "feedback_off")


def yaml_safe_load():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = {
        "enabled": True,
        "long_command": DEFAULTS["long_command"],
        "short_command": DEFAULTS["short_command"],
        "phrases": list(DEFAULTS["phrases"]),
        "suffix": DEFAULTS["suffix"],
        "feedback_on": DEFAULTS["feedback_on"],
        "feedback_off": DEFAULTS["feedback_off"],
    }
    try:
        raw = yaml_safe_load()
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        if "enabled" in raw:
            try:
                cfg["enabled"] = bool(raw["enabled"])
            except Exception:
                pass
        for key in CONFIG_TEXT_KEYS:
            if key in raw and str(raw[key] or "").strip():
                cfg[key] = str(raw[key])
        if isinstance(raw.get("phrases"), list):
            phrases = [str(p).strip().lower() for p in raw["phrases"]
                       if str(p).strip()]
            cfg["phrases"] = phrases
    _cfg_cache = cfg
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


# ── the toggles ───────────────────────────────────────────────────────────────

def parse_slash(text: str, cfg: Optional[Dict[str, Any]] = None
                ) -> Optional[str]:
    """``"on"`` / ``"off"`` when the message IS the toggle command, else None.

    Exact full-message match (case/whitespace tolerant) so chat that merely
    CONTAINS the word never flips modes.
    """
    c = cfg or DEFAULTS
    t = (text or "").strip().lower()
    if not t:
        return None
    if t == str(c.get("long_command", "/long")).strip().lower():
        return "on"
    if t == str(c.get("short_command", "/short")).strip().lower():
        return "off"
    return None


# ── one-turn phrase triggers ─────────────────────────────────────────────────

def phrase_hit(text: Optional[str], phrases: Optional[List[str]] = None
               ) -> bool:
    """True when the user's message contains one of the stretch phrases.

    Word-boundary, case-insensitive: "go on" matches "now go on about it"
    but never "gonna".
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    for phrase in (phrases if phrases is not None else DEFAULTS["phrases"]):
        p = (phrase or "").strip().lower()
        if not p:
            continue
        if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", t):
            return True
    return False


# ── the override paragraph ───────────────────────────────────────────────────

def long_suffix(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The paragraph appended LAST so it beats earlier length rules."""
    c = cfg or DEFAULTS
    suffix = str(c.get("suffix") or "").strip()
    if suffix:
        return suffix
    return DEFAULTS["suffix"]
