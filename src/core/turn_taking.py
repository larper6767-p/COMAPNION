"""Features 21+22: patient turn-taking + her listening noises.

What it does
────────────
A person doesn't answer the second you stop for breath — and a person
doesn't sit mute while you rattle on, either.  This module is the small
brain behind BOTH:

* ``decide()`` — the endpointing policy: given what Whisper thinks you
  said SO FAR (a partial transcript) and how long you've been quiet, it
  decides whether you're actually done.  An open-ended tail ("…and then
  we went to the") buys extra patience; a complete phrase ("…right? yeah"
  ) ends the turn as soon as the configured end-silence is held.
* ``pick_line()`` — the backchannel policy: mid-monologue, during a short
  pause after enough speech and not too recently, she gives a tiny
  non-turn-taking "Mhm…" / "Right…" — you keep the floor the whole time.
* ``tail_seconds()`` — slice the last N seconds out of a growing capture
  so partial Whisper calls stay cheap during long ramble-checks.

PURE core module: no Qt, no LLM, no wall-clock reads — everything takes
explicit inputs.  The wiring lives in she.py (the Whisper partials and
the Speaker playback) and recording.py (the pause-judge hook).
"""
from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "listening.yaml"

DEFAULTS: Dict[str, Any] = {
    "enabled": False,            # OFF by default: changes her listening manner
    "end_silence_s": 1.6,         # hold this long once a phrase is complete
    "max_grace_s": 3.0,           # the EXTRA patience an open-tail phrase buys
    "min_words_grace_s": 1.5,     # extra patience while almost nothing was heard
    "min_words": 2,               # fewer words than this → not done yet
    "tail_window_s": 12.0,        # how much audio the partial Whisper call sees
    "post_grace_s": 0.0,          # Feature 25: extra listening AFTER she decides
                                  # you're done (yaml ships 3.0); 0 = plain stop
    "open_words": [               # a sentence ending on one of these is open
        "and", "or", "but", "so", "cuz", "cause", "because", "like", "then",
        "when", "that", "which", "who", "the", "a", "an", "to", "of", "in",
        "on", "my", "our", "his", "her", "their", "its", "is", "was", "are",
        "um", "uh", "with", "for", "into", "from", "than", "as", "if",
        "though", "while", "since", "maybe", "probably", "kinda", "sorta",
    ],
    "backchannels": True,         # requires the top-level `enabled`
    "backchannel_from_s": 0.9,    # silence window in which a noise may fire
    "backchannel_to_s": 1.4,      # past this we're close to dispatching
    "backchannel_min_speech_s": 6.0,   # user must have been talking this long
    "backchannel_gap_s": 9.0,     # at most one every X seconds
    "mute_s": 0.7,                # mic-tail dropped after her noise plays
    "backchannel_lines": [
        "Mhm.",
        "Right, right…",
        "Yeah…",
        "No way.",
        "Really?",
        "Okay…",
        "Go on, I'm listening.",
        "I know, right?",
        "Uh-huh.",
        "O-M-G.",
    ],
}

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

def yaml_safe_load():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = dict(DEFAULTS)
    try:
        raw = yaml_safe_load()
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key in raw:
                try:
                    if key in ("enabled", "backchannels"):
                        cfg[key] = bool(raw[key])
                    elif key == "open_words":
                        words = raw[key]
                        if isinstance(words, list):
                            cfg[key] = [str(w).lower() for w in words if str(w)]
                    elif key == "backchannel_lines":
                        lines = raw[key]
                        if isinstance(lines, list):
                            cfg[key] = [str(x) for x in lines if str(x).strip()]
                    else:
                        cfg[key] = float(raw[key])
                except Exception:
                    pass
    _cfg_cache = cfg
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


# ── the endpointing policy ────────────────────────────────────────────────────

_punct_close = re.compile(r"[.!?…]\s*$")
_open_tail_chars = re.compile(r"[-,;:\u2013\u2026]\s*$")


def ends_open(partial: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True when the phrase's tail promises a continuation.

    * A closing mark (. ? ! …) settles it — even "yeah?" is a complete line.
    * A hanging trailing mark (",", "-", ";") is open.
    * Otherwise: the LAST word decides (…went to the / …and then / …so).
    """
    t = (partial or "").strip()
    if not t:
        return True                     # nothing heard yet → stay patient
    if _punct_close.search(t):
        return False                    # a finished utterance (even "yeah?")
    if _open_tail_chars.search(t):
        return True                     # hanging punctuation → mid-thought
    words = re.findall(r"[A-Za-z']+", t.lower())
    if not words:
        return True
    open_set = set((cfg or DEFAULTS).get("open_words") or []) or \
        set(DEFAULTS.get("open_words") or [])
    return words[-1] in open_set


def decide(partial: str, sil_s: float, cfg: Optional[Dict[str, Any]] = None
           ) -> Tuple[bool, str]:
    """(dispatch_now, reason) for one pause-recheck.

    ``partial`` is what Whisper has so far; ``sil_s`` the current pause
    length.  External caps (max_seconds / the extreme-silence net in
    recording.py) keep a never-dispatching situation from hanging forever.
    """
    c = cfg or {}
    end = float(c.get("end_silence_s", DEFAULTS["end_silence_s"]))
    grace = float(c.get("max_grace_s", DEFAULTS["max_grace_s"]))
    min_w_grace = float(c.get("min_words_grace_s",
                              DEFAULTS["min_words_grace_s"]))
    min_words = int(c.get("min_words", DEFAULTS["min_words"]))

    words = re.findall(r"\S+", (partial or "").strip())
    need = end
    if ends_open(partial, c):
        need = end + grace
    elif len(words) < min_words:
        need = end + min_w_grace           # not much heard yet: hmm, wait

    if sil_s >= need:
        return True, f"silence held {sil_s:.1f}s (need {need:.1f}s)"
    return False, (f"keep waiting ({need - sil_s:.1f}s: "
                   f"{'open tail' if ends_open(partial, c) else 'complete'})")


# ── her listening noises ─────────────────────────────────────────────────────

def pick_line(speech_s: float, sil_s: float, now_s: float,
              last_bc_s: Optional[float], cfg: Optional[Dict[str, Any]] = None
              ) -> Optional[str]:
    """One backchannel line to play, or None (stay silent / not allowed).

    Rules: the master `enabled` gate already happened in the caller; only
    fire INSIDE the configured silence window (long enough that she heard
    you stop, short enough that you're clearly still at it), after enough
    total speech, and not twice inside the configured gap.
    """
    c = cfg or {}
    if not c.get("backchannels", DEFAULTS["backchannels"]):
        return None
    frm = float(c.get("backchannel_from_s", DEFAULTS["backchannel_from_s"]))
    to = float(c.get("backchannel_to_s", DEFAULTS["backchannel_to_s"]))
    min_speech = float(c.get("backchannel_min_speech_s",
                             DEFAULTS["backchannel_min_speech_s"]))
    gap = float(c.get("backchannel_gap_s", DEFAULTS["backchannel_gap_s"]))

    if not (frm <= sil_s <= to):
        return None
    if speech_s < min_speech:
        return None
    if last_bc_s is not None and (now_s - last_bc_s) < gap:
        return None
    lines = c.get("backchannel_lines")
    if lines is None or not isinstance(lines, list):
        lines = DEFAULTS["backchannel_lines"]
    if not lines:
        return None                        # an emptied bank disables noises
    return random.choice(lines)


# ── audio helpers ─────────────────────────────────────────────────────────────

def tail_seconds(audio: np.ndarray, seconds: float, sample_rate: int = 16000
                 ) -> np.ndarray:
    """The last ``seconds`` of a growing capture (cheap partial Whisper)."""
    try:
        if audio is None or len(audio) == 0:
            return np.zeros(0, dtype=np.float32)
        window = max(1, int(round(float(seconds) * int(sample_rate))))
        return np.asarray(audio[-window:], dtype=np.float32)
    except Exception:
        return np.zeros(0, dtype=np.float32)
