"""Feature 5: text input — type a message instead of speaking.

Pure config loader + input normalisation + the Feature 8B bubble-slot state
machine.  The GUI/companion wiring lives in ``app.py``; a typed message is
routed through the *same* turn pipeline as a voice turn, so voice behaviour is
unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "text_input.yaml"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "mode": "inline",
    "menu_label": "\u2328\ufe0f  Type a message\u2026",
    "placeholder": "Type what you'd like to say to Evelyn\u2026",
    "inline_placeholder": "Type here\u2026 Enter to send",
    "hotkey": "ctrl+t",
    # Feature 8B: the input takes the slot back this many seconds AFTER her
    # TTS has actually finished (0 = as soon as the display timer expires).
    "return_delay": 2.0,
}

# Long messages are truncated to this many characters before being sent.
MAX_INPUT_CHARS = 1000

# "inline" = the input shares the speech-bubble slot (Feature 8B).
# "window" = the original separate dialog (Feature 5 as shipped).
MODES = ("inline", "window")


@dataclass(frozen=True)
class TextInputConfig:
    enabled: bool = bool(DEFAULTS["enabled"])
    mode: str = str(DEFAULTS["mode"])
    menu_label: str = str(DEFAULTS["menu_label"])
    placeholder: str = str(DEFAULTS["placeholder"])
    inline_placeholder: str = str(DEFAULTS["inline_placeholder"])
    hotkey: str = str(DEFAULTS["hotkey"])
    return_delay: float = float(DEFAULTS["return_delay"])


def load_config(path: Optional[Path] = None) -> TextInputConfig:
    """Load ``config/text_input.yaml`` over the built-in defaults.

    A missing/empty/malformed file yields the defaults — a bad config can never
    break startup.  An unknown ``mode`` falls back to the shipped default
    (``inline``) with a warning.
    """
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data: Any = None
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except Exception as e:
        logger.warning("text_input.yaml unreadable; using defaults: %s", e)
        data = None

    if not isinstance(data, dict):
        return TextInputConfig()

    menu_label = data.get("menu_label")
    placeholder = data.get("placeholder")
    inline_placeholder = data.get("inline_placeholder")
    hotkey = data.get("hotkey")

    raw_mode = data.get("mode")
    mode = str(raw_mode).strip().lower() if raw_mode else str(DEFAULTS["mode"])
    if mode not in MODES:
        if raw_mode:
            logger.warning(
                "text_input.yaml: unknown mode %r; using %r",
                raw_mode, DEFAULTS["mode"])
        mode = str(DEFAULTS["mode"])

    raw_delay = data.get("return_delay")
    try:
        # seconds the input waits after her TTS finished; negatives clamp to 0
        return_delay = (max(0.0, float(raw_delay))
                        if raw_delay is not None
                        else float(DEFAULTS["return_delay"]))
    except (TypeError, ValueError):
        logger.warning(
            "text_input.yaml: bad return_delay %r; using %rs",
            raw_delay, DEFAULTS["return_delay"])
        return_delay = float(DEFAULTS["return_delay"])

    return TextInputConfig(
        enabled=bool(data.get("enabled", DEFAULTS["enabled"])),
        mode=mode,
        menu_label=str(menu_label) if menu_label else str(DEFAULTS["menu_label"]),
        placeholder=str(placeholder) if placeholder else str(DEFAULTS["placeholder"]),
        inline_placeholder=(str(inline_placeholder) if inline_placeholder
                            else str(DEFAULTS["inline_placeholder"])),
        hotkey=str(hotkey) if hotkey else str(DEFAULTS["hotkey"]),
        return_delay=return_delay,
    )


def normalize_input(
    text: Optional[str], max_length: int = MAX_INPUT_CHARS
) -> Optional[str]:
    """Clean up a typed message.

    Collapses runs of whitespace (including newlines) to single spaces and
    strips the ends.  Returns ``None`` when there is nothing to send; otherwise
    the cleaned text, truncated to ``max_length`` characters.
    """
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned or None


class InlineBubbleState:
    """Feature 8B: pure state machine for the shared speech-bubble slot.

    Two widgets occupy ONE slot in the GUI: the speech label (``QLabel``) and
    the inline input (``QLineEdit``).  Every decision lives here, without Qt;
    ``app.py`` only applies the returned action:

    ==========  ==========================================================
    action      meaning
    ==========  ==========================================================
    ``speech``  show her speech label, hide the input
    ``input``   show the inline input, hide the speech label
    ``hidden``  clear the slot (a turn/status owns it, or mode is "window")
    ``retry``   the slot is busy — re-arm the hide timer and ask again
    ``noop``    do nothing
    ==========  ==========================================================

    In ``mode: window`` (or when the feature is off) the machine behaves
    exactly like Feature 5 as shipped: text shows, then the bubble simply
    hides — no input ever appears.
    """

    __slots__ = ("inline", "mode")

    def __init__(self, inline: bool = True) -> None:
        self.inline = bool(inline)
        # Logical mode, for tests/logging: "input" | "speech" | "hidden".
        self.mode = "input" if self.inline else "hidden"

    def on_show(self, text: str) -> str:
        """A reveal happened (her speech, a status, your ``💬`` echo…)."""
        if text:
            self.mode = "speech"
            return "speech"
        self.mode = "hidden"           # empty text = a turn is running
        return "hidden"

    def on_hide_timeout(self, busy: bool = False) -> str:
        """The display timer fired (speech/status finished being shown)."""
        if busy:
            return "retry"             # audio/generation still owns the slot
        if self.inline:
            self.mode = "input"
            return "input"
        self.mode = "hidden"
        return "hidden"                # window mode: today's bubble.hide()

    def on_tap(self, busy: bool = False) -> str:
        """The user tapped the visible speech label."""
        if not self.inline or busy:
            return "noop"              # never wipe her words mid-speech
        self.mode = "input"
        return "input"

    @staticmethod
    def on_submit(raw: Optional[str]) -> Optional[str]:
        """Enter pressed: the cleaned message, or ``None`` (nothing to send)."""
        return normalize_input(raw)


__all__ = [
    "TextInputConfig",
    "InlineBubbleState",
    "load_config",
    "normalize_input",
    "DEFAULT_CONFIG_PATH",
    "DEFAULTS",
    "MAX_INPUT_CHARS",
    "MODES",
]
