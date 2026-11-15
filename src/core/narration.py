"""Feature 28: silent narration — display-only stage lines.

She may act, not only talk.  Narration spans wrapped in double asterisks
(**she leans back, swirling her glass**) are shown in the bubble but NEVER
spoken aloud: the voice text and the bubble text come from the same queue
today, so segments are split at the point where words are queued for the
speaker and narration is displayed instead.

The model is taught the marker by a feature-owned prompt block
(``config/aliveness.yaml``); stripping here is defensive, so stray asterisks
can never reach the TTS queue even if the model forgets the format.

Pure helpers: no Qt, no LLM, no file access.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_SPAN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_SPACES = re.compile(r"[ \t]{2,}")


def extract_narration(text: str) -> List[str]:
    """The raw narration spans (asterisks included, display-ready).  Pure."""
    if not isinstance(text, str):
        return []
    return [f"**{m.group(1).strip()}**" for m in _SPAN.finditer(text)
            if m.group(1).strip()]


def split_voice(text: str) -> Tuple[str, List[str]]:
    """Split a frame into ``(voice_text, narration_spans)``.  Pure.

    The voice text keeps word order and punctuation; narration content is
    replaced by a single space (so ``**she leans closer** where were you``
    still speaks ``where were you``).  Narration spans are returned with the
    asterisks, ready for bubble display.
    """
    text = text or ""
    if not isinstance(text, str):
        return "", []
    notes = extract_narration(text)

    def _blank(match: "re.Match") -> str:
        m = match.group(1).strip()
        return " " if m else " "

    voice = _SPACES.sub(" ", _SPAN.sub(_blank, text)).strip()
    return voice, notes


def strip_narration(text: str) -> str:
    """All narration removed, whitespace collapsed (history-free voice)."""
    voice, _notes = split_voice(text)
    return voice


def is_narration(text: str) -> bool:
    """True when the whole text is a single narration span."""
    if not isinstance(text, str):
        return False
    s = text.strip()
    if not (s.startswith("**") and s.endswith("**") and len(s) >= 5):
        return False
    return _SPAN.fullmatch(s) is not None


__all__ = ["extract_narration", "split_voice", "strip_narration",
           "is_narration"]
