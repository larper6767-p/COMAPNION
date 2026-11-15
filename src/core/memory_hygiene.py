"""Feature 27: durable-fact hygiene for the flat memory store.

The old extractor stored whatever the model emitted, including ephemeral
statements ("I have chicken"), meta instructions ("search the internet next
time") and even lines the assistant had said.  Those facts crowded out the real
user profile and leaked assistant behavior into her voice.

This module is pure: it parses/validates candidate facts, but does not read
files, call the model, or know about Evelyn.  ``parse_facts`` accepts a JSON
list, a ``{"facts": [...]}`` object, or plain lines, and returns only durable
third-person user facts.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List

# Anything that describes the assistant, the machinery, or the roleplay itself.
_META = (
    "assistant", "language model", " as an ai", "roleplay", "role-play",
    "system prompt", "the prompt", "internet access", "search the internet",
    "web search", "website", "browser", "chatbot",
)

# Time-bound / conversational clutter.  A fact that needs "right now" to be true
# is not a durable fact.
_EPHEMERAL = (
    "right now", "currently", "today", "tonight", "last night", "this morning",
    "this afternoon", "tomorrow", "yesterday", "just now", "at the moment",
    "i have chicken", "i'm planning", "i'm thinking", "i'm studying",
    "i'm gonna", "i'm going to", "i want to talk", "i'm feeling", "my head is",
    "i'll probably", "i'm trying to", "i'm looking into", "i told you",
    "i thought i'd", "i have to", "i need to", "i'm about to",
)

_VAGUE = {
    "i", "me", "you", "it", "this", "that", "i like to think",
    "i feel very close at it", "i will stay", "i will never be mad at you",
    "lives alone",  # bare fragment; the curated form is "The user lives alone"
    "none", "null", "n/a", "nothing", "no facts", "no durable facts",
    "i don't know", "i do not know", "nothing to add",
}

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")
_SPACE = re.compile(r"\s+")


def clean_fact(text: Any) -> str:
    """Normalize one candidate fact without changing its meaning."""
    if not isinstance(text, str):
        return ""
    s = text.strip().strip('"').strip("'")
    s = _BULLET.sub("", s)
    s = _SPACE.sub(" ", s).strip()
    if s.endswith("."):
        s = s[:-1].rstrip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


def is_durable_fact(text: Any) -> bool:
    """True only for a specific, durable, user-owned fact."""
    s = clean_fact(text)
    if not (4 <= len(s) <= 180):
        return False
    if "\n" in s or s.endswith("?"):
        return False
    low = s.lower()
    if low in _VAGUE:
        return False
    if low.startswith(("none", "nothing", "no facts", "n/a", "no durable")):
        return False
    if any(marker in low for marker in _META):
        return False
    if any(marker in low for marker in _EPHEMERAL):
        return False
    if low.startswith(("evelyn ", "assistant ", "you ")):
        return False
    return True


def _candidates(raw: Any) -> Iterable[Any]:
    """Yield candidate facts from the model's JSON or line-based reply."""
    if isinstance(raw, list):
        yield from raw
        return
    if isinstance(raw, dict):
        for key in ("facts", "items", "memories"):
            if isinstance(raw.get(key), list):
                yield from raw[key]
                return
        return
    text = str(raw or "").strip()
    if not text:
        return
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    body = (fenced.group(1) if fenced else text).strip()
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if isinstance(data, (list, dict)):
        yield from _candidates(data)
        return
    for line in body.splitlines():
        line = line.strip()
        if line:
            yield line


def parse_facts(raw: Any) -> List[str]:
    """Return clean, deduplicated durable facts from a model reply."""
    out: List[str] = []
    seen = set()
    for candidate in _candidates(raw):
        fact = clean_fact(candidate)
        if not is_durable_fact(fact):
            continue
        key = fact.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


__all__ = ["clean_fact", "is_durable_fact", "parse_facts"]
