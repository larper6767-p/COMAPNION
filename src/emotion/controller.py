"""Config-driven mood -> expression mapping (Feature 3).

This is deliberately **not** a parallel expression system.  The renderer
(``live2d_face.py``) keeps ownership of the model and of ``SetExpression()`` /
``ResetExpression()``; this module is pure logic that answers, from
``config/emotions.yaml``:

* which ``.exp3.json`` files should be registered (filename -> key);
* which expression key (if any) should play for a given mood.

The built-in defaults reproduce the values that used to be hard-coded in
``live2d_face.py``, so with no config file (or a partial one) the behaviour is
exactly as before.

Notes
-----
* ``enabled: false`` disables expression playback entirely; moods still change
  the face through their parameter maps in ``config/live2d_moods.json``.
* ``mood_expressions`` / ``expressions`` are **merged over** the defaults, so you
  can override just the entries you care about.  ``null`` for a mood means "no
  expression"; ``null`` for a file entry removes it from registration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "emotions.yaml"

DEFAULT_ENABLED = True

# ── Built-in defaults (previously hard-coded in live2d_face.py) ────────────────
DEFAULT_EXPRESSIONS: Dict[str, str] = {
    "脸红.exp3.json":   "blush",
    "生气.exp3.json":   "angry",
    "星星眼.exp3.json": "star_eyes",
    "爱心眼.exp3.json": "heart_eyes",
    "泪.exp3.json":     "tears",
    "白眼.exp3.json":   "eyeroll",
    "脸黑.exp3.json":   "dark",
    "血.exp3.json":     "blood",
    "右手.exp3.json":   "right_hand",
    "左手.exp3.json":   "left_hand",
}

DEFAULT_MOOD_EXPRESSIONS: Dict[str, Optional[str]] = {
    "idle":        None,
    "joyful":      "blush",
    "excited":     "star_eyes",
    "embarrassed": "blush",
    "sad":         "tears",
    "angry":       "angry",
    "surprised":   None,
    "curious":     None,
    "relaxed":     None,
    "love":        "heart_eyes",
    "aroused":     "blush",
}


@dataclass(frozen=True)
class EmotionsConfig:
    """Resolved emotion/expression settings."""

    enabled: bool = DEFAULT_ENABLED
    expressions: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_EXPRESSIONS))
    mood_expressions: Dict[str, Optional[str]] = field(
        default_factory=lambda: dict(DEFAULT_MOOD_EXPRESSIONS))

    def expression_for(self, mood: str) -> Optional[str]:
        """Expression key to play for ``mood`` — None when disabled or unmapped."""
        if not self.enabled:
            return None
        return self.mood_expressions.get(mood)

    def key_for_file(self, filename: str) -> Optional[str]:
        """Registered key for a ``.exp3.json`` filename, or None if not registered."""
        return self.expressions.get(filename)

    def all_moods(self) -> list:
        return sorted(self.mood_expressions)


def next_expression_action(
    cfg: EmotionsConfig, mood: str, active: Optional[str]
):
    """Decide what the renderer should do for ``mood``.

    Returns one of:
      * ``("set", key)``   — call ``SetExpression(key)``
      * ``("reset", None)``— call ``ResetExpression()``
      * ``None``           — no change needed (already showing the right thing)

    Pure so it can be unit-tested without a GL context; the renderer only
    performs the returned action.
    """
    key = cfg.expression_for(mood)
    if key == active:
        return None
    return ("set", key) if key is not None else ("reset", None)


def load_config(path: Optional[Path] = None) -> EmotionsConfig:
    """Load ``config/emotions.yaml`` merged over the built-in defaults.

    A missing, empty or malformed file yields the built-in defaults (identical to
    the previous hard-coded behaviour) — a bad config can never break startup.
    """
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = None
    try:
        import yaml  # local: only needed when a config file exists

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except Exception as e:
        logger.warning("emotions.yaml unreadable; using built-in defaults: %s", e)
        data = None

    if not isinstance(data, dict):
        return EmotionsConfig()

    enabled = bool(data.get("enabled", DEFAULT_ENABLED))

    expressions = dict(DEFAULT_EXPRESSIONS)
    raw_expressions = data.get("expressions")
    if isinstance(raw_expressions, dict):
        for filename, key in raw_expressions.items():
            name = str(filename)
            if key is None or key is False:
                expressions.pop(name, None)          # explicit removal
            else:
                expressions[name] = str(key)

    mood_expressions = dict(DEFAULT_MOOD_EXPRESSIONS)
    raw_moods = data.get("mood_expressions")
    if isinstance(raw_moods, dict):
        for mood, key in raw_moods.items():
            mood_expressions[str(mood)] = None if key is None else str(key)

    return EmotionsConfig(
        enabled=enabled,
        expressions=expressions,
        mood_expressions=mood_expressions,
    )


__all__ = [
    "EmotionsConfig",
    "load_config",
    "next_expression_action",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENABLED",
    "DEFAULT_EXPRESSIONS",
    "DEFAULT_MOOD_EXPRESSIONS",
]
