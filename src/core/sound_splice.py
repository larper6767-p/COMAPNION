"""Feature 12c: splice SFX cues into a sentence at the exact word.

Feature 12 played one clip when a sentence STARTED.  This module finds the
trigger positions INSIDE the sentence — the four sound-script symbols
(``~ ♥ ♡ ♪``) and the onomatopoeia regexes from ``config/sfx.yaml`` — and
turns the sentence into an ordered part list:

    "I missed you... mwah. Get back here."
        -> [("say", "I missed you... "), ("cue", kiss),
            ("say", ". Get back here.")]

The TTS loop speaks only the say-parts; the playback loop plays the cue
exactly where the word was, so the spoken word is replaced by its sound.

A token is spliced ONLY when a real clip exists for it (``has_clip``);
otherwise it stays in the speech text and is spoken as before — nothing is
ever silently dropped.  Pure module: no pygame, no network, no disk.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

# A part is ("say", fragment_text) or ("cue", ((category, stems), ...)).
Part = Tuple[str, object]
Hit = Tuple[str, Tuple[str, ...]]


def _find_spans(text: str, cfg, has_clip: Optional[Callable]) -> List[Tuple]:
    """(start, end, category, stems) for every usable trigger, in text order."""
    spans: List[Tuple[int, int, str, Tuple[str, ...]]] = []
    for marker, stems in getattr(cfg, "triggers", ()):
        if not marker:
            continue
        for m in re.finditer(re.escape(str(marker)), text):
            if m.end() > m.start():
                spans.append((m.start(), m.end(), str(marker), tuple(stems)))
    for pat, stems in getattr(cfg, "words", ()):
        try:
            matches = list(pat.finditer(text))
        except Exception:
            continue
        for m in matches:
            if m.end() > m.start():
                spans.append((m.start(), m.end(), pat.pattern, tuple(stems)))

    # earliest first; on a tie keep the longest token
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    cap = max(1, int(getattr(cfg, "max_per_sentence", 4)))
    out: List[Tuple[int, int, str, Tuple[str, ...]]] = []
    last = -1
    for span in spans:
        if span[0] < last:
            continue                       # overlaps a span already accepted
        if has_clip is not None:
            try:
                if not has_clip(span[3]):
                    continue               # no file -> leave the word spoken
            except Exception:
                continue
        out.append(span)
        last = span[1]
        if len(out) >= cap:
            break
    return out


def split_text(text: Optional[str], cfg,
               has_clip: Optional[Callable] = None) -> List[Part]:
    """Split a sentence into say/cue parts at its sound-script triggers.

    ``has_clip(stems) -> bool`` gates the splice (injectable for tests);
    ``None`` means "assume a clip exists" (pure default).  Returns a single
    say-part unchanged when the feature is off, the text is empty, or no
    trigger is present.
    """
    text = "" if text is None else str(text)
    if not text or not getattr(cfg, "enabled", False):
        return [("say", text)]

    spans = _find_spans(text, cfg, has_clip)
    if not spans:
        return [("say", text)]

    parts: List[Part] = []
    cursor = 0
    for start, end, category, stems in spans:
        if start > cursor:
            parts.append(("say", text[cursor:start]))
        parts.append(("cue", ((category, stems),)))
        cursor = end
    if cursor < len(text):
        parts.append(("say", text[cursor:]))
    return parts


__all__ = ["split_text", "Part", "Hit"]
