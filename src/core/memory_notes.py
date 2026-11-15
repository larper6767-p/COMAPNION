"""Feature 29: she decides what to remember — self-written memory notes.

Passive extraction made her a diary.  This adds a self-authored memory
channel: at the end of a turn she may write a private note

    {{remember: The user's photography set is called 'Moon Morning'}}

which is (a) never spoken, (b) never displayed, (c) filtered through the same
durable-fact hygiene as auto-extraction (``core/memory_hygiene``), and
(d) stored per-day-capped when a turn completes.

The marker deliberately does NOT use square brackets: ``[...]`` is the body/mood
tag channel, and unknown bracket tags get dropped by the tag parser.  Curly
braces survive parsing untouched.

Pure module: no Qt, no LLM, no file access.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from core.memory_hygiene import clean_fact, is_durable_fact, parse_facts

_MARKER = re.compile(
    r"\{\{\s*remember\s*:\s*(.+?)\s*\}\}", re.IGNORECASE | re.DOTALL)


def extract_notes(text: str) -> List[str]:
    """Raw self-note contents (marker braces removed).  Order preserved."""
    if not isinstance(text, str):
        return []
    return [m.group(1).strip() for m in _MARKER.finditer(text)
            if m.group(1).strip()]


def strip_notes(text: str) -> str:
    """Remove memory notes; collapse the whitespace they leave behind."""
    if not isinstance(text, str):
        return ""
    cleaned = _MARKER.sub(" ", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return re.sub(r"\n[ \t]*\n[ \t]*\n", "\n\n", cleaned).strip()


# Kept alongside the rest so call sites can do one import dance.
parse_fact_candidates = parse_facts
clean = clean_fact
durable = is_durable_fact


def filter_notes(
    raw_notes: List[str],
    existing_keys: Optional[set] = None,
) -> List[str]:
    """Durable, deduplicated self-note facts, leaning on the hygiene filter."""
    out: List[str] = []
    seen = {k.casefold() for k in (existing_keys or ())}
    for raw in raw_notes:
        fact = clean_fact(raw)
        if not fact or not is_durable_fact(fact):
            continue
        key = fact.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def split_note_and_text(text: str) -> Tuple[str, List[str]]:
    """``(voice_text_without_notes, raw_note_texts)``.  Convenience split."""
    return strip_notes(text), extract_notes(text)


__all__ = [
    "extract_notes", "strip_notes", "filter_notes", "split_note_and_text",
]
