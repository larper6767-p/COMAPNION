"""Feature 13 Stage B: touch-reaction escalation + mapping (pure).

No Qt, no GL, no clock reads — every decision takes an explicit ``now`` and a
seedable state object is not needed (the machine is deterministic given its
own counters).

Pipeline per touch (the app calls in this order):

    touch_reactions.gate(kind, speaking, listening, dnd)
        → "full" | "startle_only" | "ignore"
    TouchEscalation.process(now, kind, duration=…, state=BehaviorState)
        → Reaction | None           (None = nothing to do this time)

Escalation
* pokes  — a decaying counter (≈15 s half-life): 1 poke = ``surprised``,
  ≈2.5 = ``playful_protest`` (a second/third poke), ≈4.5 = ``shrug_off``
  (she's done with the poking).
* pats   — credit accumulates per hold duration (~30 s half-life): first
  touch = ``pat_soft_smile``; ≈2 s total = ``pat_blush`` (blush + eyes
  soften); ≈3.5 s = ``pat_stop`` (playful "okay, that's enough") and a
  short lockout.
* state bias (BehaviorState): a SAD valence reaches pat warmth sooner
  (thresholds × ``pat_threshold_mult_sad``); a tired/low-arousal state
  starts protesting pokes sooner (poke thresholds × ``poke_threshold_mult_tired``).

While she is SPEAKING, only a small startle may pass (``STARTLE_WHILE_
SPEAKING``) and nothing ever touches her audio.  While recording, all
touch reactions are ignored.  DND disables everything.

Mapping routes to EXISTING body actions (``fx:blush`` ``gesture:nod`` … the
same tags/keys body_actions resolves) plus two NEW procedural gestures
handled through the behavior director: ``content`` (eyes soft-close + small
head lean) and ``startle`` (quick eye widen + head pull-back).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "touch_reactions.json"

# ── escalation config ────────────────────────────────────────────────────────

ESC_DEFAULTS = {
    "poke_half_life_s": 15.0,     # decay of the poke counter
    "poke_levels": [1.0, 2.5, 4.5],   # counter → surprised / protest / shrug
    "pat_levels_s": [0.8, 2.0, 3.5],  # pat credit → smile / blush / stop
    "pat_credit_half_life_s": 30.0,
    "lockout_seconds": 12.0,      # after pat_stop: nothing reacts for a while
    "sad_valence": -0.25,         # valence at/below this = "sad" bias
    "pat_threshold_mult_sad": 0.7,    # sadder → warmth arrives sooner
    "tired_arousal": 0.3,         # arousal at/below this = "tired" bias
    "poke_threshold_mult_tired": 0.8, # tired → push back on pokes sooner
}


@dataclass(frozen=True)
class EscalationConfig:
    poke_half_life_s: float = float(ESC_DEFAULTS["poke_half_life_s"])
    poke_levels: Tuple[float, float, float] = (1.0, 2.5, 4.5)
    pat_levels_s: Tuple[float, float, float] = (0.8, 2.0, 3.5)
    pat_credit_half_life_s: float = float(ESC_DEFAULTS["pat_credit_half_life_s"])
    lockout_seconds: float = float(ESC_DEFAULTS["lockout_seconds"])
    sad_valence: float = float(ESC_DEFAULTS["sad_valence"])
    pat_threshold_mult_sad: float = float(ESC_DEFAULTS["pat_threshold_mult_sad"])
    tired_arousal: float = float(ESC_DEFAULTS["tired_arousal"])
    poke_threshold_mult_tired: float = float(
        ESC_DEFAULTS["poke_threshold_mult_tired"])


def load_escalation(path: Optional[Path] = None) -> EscalationConfig:
    """Read the ``escalation`` section of config/touch_reactions.json.
    Missing/broken sections fall back to the defaults section-by-section."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("escalation", {})
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    def num(key, default, lo, hi):
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            return float(default)
        if v != v:                       # NaN guard
            return float(default)
        return max(lo, min(hi, v))

    def levels(key, default):
        vals = raw.get(key, default)
        if not isinstance(vals, (list, tuple)) or len(vals) != 3:
            return tuple(default)
        try:
            out = tuple(max(0.0, float(v)) for v in vals)
        except (TypeError, ValueError):
            return tuple(default)
        # keep them sorted and strictly increasing
        a, b, c = sorted(out)
        if not (a < b < c):
            return tuple(default)
        return (a, b, c)

    return EscalationConfig(
        poke_half_life_s=num("poke_half_life_s",
                             float(ESC_DEFAULTS["poke_half_life_s"]), 1.0, 600.0),
        poke_levels=levels("poke_levels", ESC_DEFAULTS["poke_levels"]),
        pat_levels_s=levels("pat_levels_s", ESC_DEFAULTS["pat_levels_s"]),
        pat_credit_half_life_s=num(
            "pat_credit_half_life_s",
            float(ESC_DEFAULTS["pat_credit_half_life_s"]), 1.0, 3600.0),
        lockout_seconds=num("lockout_seconds",
                            float(ESC_DEFAULTS["lockout_seconds"]), 0.0, 600.0),
        sad_valence=num("sad_valence", float(ESC_DEFAULTS["sad_valence"]),
                        -1.0, 0.0),
        pat_threshold_mult_sad=num("pat_threshold_mult_sad",
                                   float(ESC_DEFAULTS["pat_threshold_mult_sad"]),
                                   0.1, 1.0),
        tired_arousal=num("tired_arousal", float(ESC_DEFAULTS["tired_arousal"]),
                          0.0, 1.0),
        poke_threshold_mult_tired=num(
            "poke_threshold_mult_tired",
            float(ESC_DEFAULTS["poke_threshold_mult_tired"]), 0.1, 1.0),
    )


# ── gate ──────────────────────────────────────────────────────────────────────

def gate(kind: str, speaking: bool, listening: bool, dnd: bool) -> str:
    """Job for one classified touch: ``full`` / ``startle_only`` / ``ignore``.

    * DND disables touch reactions entirely.
    * while the mic is recording everything is ignored,
    * while she is speaking only a small startle on a poke is allowed and her
      audio is never touched.
    """
    if dnd or listening:
        return "ignore"
    if speaking:
        return "startle_only" if kind == "poke" else "ignore"
    return "full"


# ── reactions ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reaction:
    name: str                          # for body_log lines
    actions: Tuple[str, ...] = ()      # existing body-action keys ("fx:blush"…)
    gesture: Optional[str] = None      # "content" | "startle" (director)
    valence: float = 0.0               # small impulses for BehaviorState
    arousal: float = 0.0


POKE_LEVELS = ("surprised", "playful_protest", "shrug_off")
PAT_LEVELS = ("pat_soft_smile", "pat_blush", "pat_stop")

REACTION_SPECS = {
    "surprised": Reaction(
        "surprised",
        actions=("look:up",),
        gesture="startle",
        valence=-0.01, arousal=0.10),
    "playful_protest": Reaction(
        "playful_protest",
        actions=("gesture:shake",),
        gesture=None,
        valence=-0.03, arousal=0.08),
    "shrug_off": Reaction(
        "shrug_off",
        actions=("gesture:tilt",),
        gesture=None,
        valence=-0.01, arousal=0.02),
    "pat_soft_smile": Reaction(
        "pat_soft_smile",
        actions=("gesture:nod",),
        gesture="content",
        valence=0.06, arousal=-0.05),
    "pat_blush": Reaction(
        "pat_blush",
        actions=("fx:blush",),
        gesture="content",
        valence=0.10, arousal=-0.03),
    "pat_stop": Reaction(
        "pat_stop",
        actions=("gesture:shake",),
        gesture=None,
        valence=0.00, arousal=-0.10),
}

# While she is speaking a poke may only produce a SMALL startle: a tiny eye
# widen + head pull-back, nothing else, and never any audio interference.
STARTLE_WHILE_SPEAKING = Reaction(
    "startle", actions=(), gesture="startle", valence=0.0, arousal=0.05)


def startle_only(reaction: Reaction) -> Reaction:
    """Collapse any reaction to the speaking-safe small startle."""
    return STARTLE_WHILE_SPEAKING


def resolve(reaction: Reaction, available) -> Reaction:
    """Prune the proposal to actions the model actually supports.

    ``available`` is an iterable of enabled body-action keys (e.g.
    ``{"fx:blush", "gesture:shake", ...}``).  Procedural director gestures
    (``content`` / ``startle``) are always kept.  Empty fallbacks: with no
    available-set information the actions are kept as proposed.
    """
    available = set(available) if available is not None else None
    keep = tuple(a for a in reaction.actions
                 if available is None or a in available)
    return Reaction(reaction.name, keep, reaction.gesture,
                    reaction.valence, reaction.arousal)


# ── escalation state machine ─────────────────────────────────────────────────

def _decay(value: float, dt: float, half_life: float) -> float:
    """Exponential decay with a cap so it never UNDERFLOWS into negatives."""
    if value <= 0.0 or dt <= 0.0 or half_life <= 0.0:
        return value
    return value * (0.5 ** (min(dt, 1e6) / half_life))


class TouchEscalation:
    """Pure: pokes / pats counters, threshold bands, state bias, lockout.

    ``state`` is optional; when given it must expose ``valence``/``arousal``
    (BehaviorState does).  One instance per app run; every call carries its
    own ``now`` so tests drive a fake clock.
    """

    def __init__(self) -> None:
        self.poke_count = 0.0
        self.pat_credit = 0.0
        self._last_poke_t: Optional[float] = None
        self._last_credit_t: Optional[float] = None
        self._lockout_until: Optional[float] = None

    # ── helpers ────────────────────────────────────────────────────────────
    def _decay_pokes(self, now: float, cfg: EscalationConfig) -> None:
        if self._last_poke_t is not None:
            self.poke_count = _decay(self.poke_count, max(0.0, now - self._last_poke_t),
                                     cfg.poke_half_life_s)
        self._last_poke_t = now

    def _decay_pat(self, now: float, cfg: EscalationConfig) -> None:
        if self._last_credit_t is not None:
            self.pat_credit = _decay(self.pat_credit,
                                     max(0.0, now - self._last_credit_t),
                                     cfg.pat_credit_half_life_s)
        self._last_credit_t = now

    def _locked(self, now: float) -> bool:
        return self._lockout_until is not None and now < self._lockout_until

    # ── main entry ─────────────────────────────────────────────────────────
    def process(self, now: float, kind: str, duration: float = 0.0,
                state=None, cfg: Optional[EscalationConfig] = None) -> \
            Optional[Reaction]:
        """One touch → Reaction or None (none = below escalation thresholds
        or inside the post-``pat_stop`` lockout)."""
        cfg = cfg if cfg is not None else get_escalation()

        if self._locked(now):
            return None

        if kind == "pat_end":
            # Credit only the PAST portion of a long hold (decays like the rest).
            self._decay_pat(now, cfg)
            self.pat_credit = max(0.0, self.pat_credit + max(0.0, duration))
            return None

        if kind == "poke":
            self._decay_pokes(now, cfg)
            self.poke_count += 1.0
            mult = 1.0
            aro = _num(state, "arousal")
            if aro is not None and aro <= cfg.tired_arousal:
                mult = min(mult, cfg.poke_threshold_mult_tired)
            thresholds = tuple(v * mult for v in cfg.poke_levels)
            idx = 0
            for i, thr in enumerate(thresholds):
                if self.poke_count >= thr:
                    idx = i
            return REACTION_SPECS[POKE_LEVELS[idx]]

        if kind == "pat":
            self._decay_pat(now, cfg)
            self.pat_credit = max(0.0, self.pat_credit + max(0.0, duration))
            val = _num(state, "valence")
            mult = (cfg.pat_threshold_mult_sad
                    if val is not None and val <= cfg.sad_valence else 1.0)
            thresholds = tuple(v * mult for v in cfg.pat_levels_s)
            idx = 0
            for i, thr in enumerate(thresholds):
                if self.pat_credit >= thr:
                    idx = i
            reaction = REACTION_SPECS[PAT_LEVELS[idx]]
            if idx == len(PAT_LEVELS) - 1:
                self._lockout_until = now + cfg.lockout_seconds
            return reaction

        return None

    # ── inspection (tests + body_log) ───────────────────────────────────────
    def snapshot(self) -> dict:
        return {"poke_count": round(self.poke_count, 2),
                "pat_credit": round(self.pat_credit, 2),
                "locked": self._lockout_until is not None and
                          self._lockout_until > 0.0 and True}


def _num(state, key: str) -> Optional[float]:
    try:
        v = getattr(state, key)
        return float(v)
    except (AttributeError, TypeError, ValueError):
        return None


# ── escalation config cache (hot-reload) ─────────────────────────────────────

_cached_cfg: Optional[EscalationConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_escalation(path: Optional[Path] = None) -> EscalationConfig:
    global _cached_cfg, _cached_path, _cached_mtime
    realpath = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = realpath.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _cached_cfg is not None and realpath == _cached_path and mtime == _cached_mtime:
        return _cached_cfg
    _cached_cfg = load_escalation(realpath)
    _cached_path = realpath
    _cached_mtime = mtime
    return _cached_cfg


__all__ = [
    "EscalationConfig", "load_escalation", "get_escalation",
    "gate", "Reaction", "REACTION_SPECS", "resolve", "startle_only",
    "STARTLE_WHILE_SPEAKING", "TouchEscalation",
    "POKE_LEVELS", "PAT_LEVELS", "DEFAULT_CONFIG_PATH",
    # Stage C
    "SpokenConfig", "load_spoken", "get_spoken", "tier", "pick_line",
    "VoiceGate",
]


# ── Feature 13 Stage C: spoken reactions ─────────────────────────────────────
#
# Tagged line pools in config/touch_reactions.json (``spoken`` section), keyed
# by reaction × closeness tier (``cold`` / ``warm`` / ``close`` from the
# relationship closeness value).  Lines are spoken through ``Speaker.say()``
# (its TTSCache LRU keeps repeats cheap) with a per-reaction mood tag, gated by
# a min interval (10 s) and an hourly cap (6).  NO LLM call is involved by
# default (``touch_llm_reaction: false``) and one is never wired in Stage C.
#
# Lines must stay PG-13, playful, and never suggestive or reproachful.

SPOKEN_DEFAULTS = {
    "touch_voice": True,
    "touch_llm_reaction": False,
    "min_interval_s": 10.0,
    "hourly_cap": 6,
    "warm_at": 60,             # closeness >= warm_at → "warm" tier
    "close_at": 120,           # closeness >= close_at → "close" tier
}

SPOKEN_MOODS = {
    "surprised": "surprised",
    "playful_protest": "embarrassed",
    "shrug_off": "idle",
    "pat_soft_smile": "joyful",
    "pat_blush": "embarrassed",
    "pat_stop": "embarrassed",
}

SPOKEN_LINES = {
    "surprised": {
        "cold": ["...Yes?", "Noted. A poke.", "Can I help you?"],
        "warm": ["Ah! ...Was that necessary?", "Hey! I'm ticklish there, you know.", "Wha— fine, you got my attention."],
        "close": ["Hyah—! Don't sneak up on me like that.", "Eep! Warn a girl before you poke her.", "I felt that, you know. Rude.", "My hair! Okay, I'm awake, I'm awake."],
    },
    "playful_protest": {
        "cold": ["...Please stop that.", "That's enough of that.", "Noted. Still no."],
        "warm": ["Hey, hands off the merchandise.", "One more and I'm biting. Probably.", "Persistent, aren't you?"],
        "close": ["Okay okay, I get it, cut it out!", "Poking me won't make me type faster, you know.", "Bossy little... fine, fine, what do you want?", "I'm going to start charging per poke."],
    },
    "shrug_off": {
        "cold": [".....", "Unbothered. Mostly."],
        "warm": ["...I'm just going to pretend I didn't feel that.", "You must be very bored, huh."],
        "close": ["Okay, executive decision: ignoring you now.", "I've officially stopped feeling that.", "Poke away. I have chores to finish."],
    },
    "pat_soft_smile": {
        "cold": ["Oh. That's new.", "Well. Hello to you too.", "...I'll allow it."],
        "warm": ["Oh. ...Thanks, I guess.", "That's... unexpectedly nice.", "Mm. Good job today, me?"],
        "close": ["Mm... that's nice. Don't stop.", "Hey, you. That feels good, actually.", "Careful, I could get used to this.", "See? I'm not all cramps and deadlines."],
    },
    "pat_blush": {
        "cold": ["...I'm blushing. Don't make a thing of it.", "That's... kind, I guess."],
        "warm": ["Hey... don't look at my face right now.", "This is... fine. Totally fine.", "You're doing that on purpose, aren't you."],
        "close": ["S-stop that, my face is getting warm...", "Hey! I'm supposed to be the composed one here...", "You're going to make me melt, and I still have work to do.", "D-don't say it out loud. Just keep petting."],
    },
    "pat_stop": {
        "cold": ["Okay. That's enough of that.", "We're done here, politely."],
        "warm": ["Okay, headpats are over. Not bad, though.", "That's plenty — don't push your luck.", "Alright, stopping you there. Hands to yourself."],
        "close": ["Okay, that's enough — I've got a deadline and you're not helping.", "Alright, alright! The headpets are closed for business today.", "You're worse than a cat. Shoo. ...Love you. Now shoo.", "Okay, okay, mercy! Quit it."],
    },
}

TIERS = ("cold", "warm", "close")


@dataclass(frozen=True)
class SpokenConfig:
    enabled: bool = True                       # config key: touch_voice
    llm_reaction: bool = False                 # config key: touch_llm_reaction
    min_interval_s: float = 10.0
    hourly_cap: int = 6
    warm_at: float = 60.0
    close_at: float = 120.0
    moods: Tuple[Tuple[str, str], ...] = tuple(SPOKEN_MOODS.items())
    lines: Tuple[Tuple[str, Tuple[Tuple[str, Tuple[str, ...]], ...]], ...] = (
        tuple((name, tuple(layer.items()))
              for name, layer in SPOKEN_LINES.items()))


def _lines(raw) -> Tuple:
    """reaction -> tier -> tuple(lines).

    When the ``lines`` key is absent entirely the shipped pools are used.
    Once the user declares pools, they are authoritative: a missing tier (or
    reaction) stays empty and ``pick_line`` falls back across tiers instead.
    Unknown reaction names are pruned.
    """
    if not isinstance(raw, dict):
        src = SPOKEN_LINES
    else:
        src = raw
    out = []
    for reaction in (POKE_LEVELS + PAT_LEVELS):
        r = src.get(reaction, {})
        if not isinstance(r, dict):
            continue
        tiers = []
        for tname in TIERS:
            pool = r.get(tname, ())
            if isinstance(pool, (list, tuple)):
                pool = tuple(str(x) for x in pool if str(x).strip())
            else:
                pool = ()
            tiers.append((tname, pool))
        if any(pool for _t, pool in tiers):
            out.append((reaction, tuple(tiers)))
    return tuple(out)


def load_spoken(path: Optional[Path] = None) -> SpokenConfig:
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("spoken", {})
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    def num(key, default, lo, hi, integer=False):
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            v = float(default)
        if v != v:                              # NaN guard
            v = float(default)
        v = max(lo, min(hi, v))
        return int(v) if integer else v

    moods_raw = raw.get("moods") if isinstance(raw.get("moods"), dict) else {}
    moods = []
    for reaction in (POKE_LEVELS + PAT_LEVELS):
        moods.append((reaction, str(moods_raw.get(reaction,
                     SPOKEN_MOODS.get(reaction, "idle")))))
    return SpokenConfig(
        enabled=bool(raw.get("touch_voice", True)),
        llm_reaction=bool(raw.get("touch_llm_reaction", False)),
        min_interval_s=num("min_interval_s", 10.0, 0.0, 600.0),
        hourly_cap=num("hourly_cap", 6, 0, 100, integer=True),
        warm_at=num("warm_at", 60.0, 0.0, 1000.0),
        close_at=num("close_at", 120.0, 0.0, 2000.0),
        moods=tuple(moods),
        lines=_lines(raw.get("lines")),
    )


_cached_spoken: Optional[SpokenConfig] = None
_cached_spoken_path: Optional[Path] = None
_cached_spoken_mtime: float = -1.0


def get_spoken(path: Optional[Path] = None) -> SpokenConfig:
    global _cached_spoken, _cached_spoken_path, _cached_spoken_mtime
    realpath = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = realpath.stat().st_mtime
    except OSError:
        mtime = -1.0
    if (_cached_spoken is not None and realpath == _cached_spoken_path
            and mtime == _cached_spoken_mtime):
        return _cached_spoken
    _cached_spoken = load_spoken(realpath)
    _cached_spoken_path = realpath
    _cached_spoken_mtime = mtime
    return _cached_spoken


def tier(closeness: float, warm_at: float = 60.0, close_at: float = 120.0) -> str:
    """cold / warm / close from the relationship closeness value."""
    try:
        c = float(closeness)
    except (TypeError, ValueError):
        c = 0.0
    if c >= close_at:
        return "close"
    if c >= warm_at:
        return "warm"
    return "cold"


def pick_line(spoken: SpokenConfig, reaction: str, tier_name: str,
              rng) -> Optional[str]:
    """One line from the pool (fallback to warmer→colder tiers when the
    requested tier has none).  ``rng`` needs ``choice``."""
    lines_map = dict(spoken.lines)
    pools = dict(lines_map.get(reaction, ())) if lines_map else {}
    if not pools:                       # unknown reaction → try global pools pool
        return None
    order = [tier_name] + [t for t in ("close", "warm", "cold") if t != tier_name]
    for t in order:
        pool = pools.get(t) or ()
        if pool:
            return rng.choice(tuple(pool))
    return None


class VoiceGate:
    """Pure rate limiter: min interval + hourly cap (explicit ``now`` only)."""

    def __init__(self) -> None:
        self._last: Optional[float] = None
        self._window: list = []

    def allow(self, now: float, spoken: Optional[SpokenConfig] = None) -> bool:
        s = spoken if spoken is not None else get_spoken()
        if s.min_interval_s > 0 and self._last is not None:
            if now - self._last < s.min_interval_s:
                return False
        if s.hourly_cap <= 0:
            return False
        # prune the ring to the last hour
        while self._window and now - self._window[0] >= 3600.0:
            self._window.pop(0)
        return len(self._window) < s.hourly_cap

    def record(self, now: float, spoken: Optional[SpokenConfig] = None) -> None:
        s = spoken if spoken is not None else get_spoken()
        self._last = now
        self._window.append(now)
        while len(self._window) > max(0, s.hourly_cap):
            self._window.pop(0)

