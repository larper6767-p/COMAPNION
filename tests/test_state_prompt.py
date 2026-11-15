"""Phase 3a + 3b tests: mood-vocabulary consistency and state-aware prompt.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_state_prompt.py -v

Phase 3a — Mood vocabulary consistency (single source of truth):
  * [mood:love] / [mood:idle] resolve identically through the shared
    parse_mood_tag path (core.model) and the body path (body_tags.resolve_mood).
  * idle is resolvable + falls back to sprite/TTS/body but is NEVER offered to
    the model (excluded from core.config._DEFAULTS["moods"], which derives from
    body_actions.json).  love IS offered and also drives the body path.
  * body_actions.json is the single source: config derives from it, and
    live2d_moods.json already has idle/love/aroused (no duplicates needed).

Phase 3b — State-aware personality (state_in_prompt):
  * FeelingsBucket: all nine (valence, arousal) buckets produce a short
    self-feeling phrase describing ONLY her own feeling.
  * Hysteresis: a bucket holds near a boundary and flips only after crossing.
  * phrase never emits ignored/wandered/away words.
  * BehaviorSnapshot is immutable even while the source BehaviorState mutates.
  * PetCompanion._state_line gating: off by default -> None; honours the BODY
    gate + state_in_prompt; the emitted line carries the do-not-mention
    instruction and is built from the immutable snapshot, not the live object.
  * Loop-stability: a stub LLM that always tags the mood the state suggests
    for 50 turns never pins valence/arousal to an extreme (decay keeps it
    bounded).

Deliberately pure for 3a and most of 3b (no QApplication / GL / model / audio).
The _state_line gating test only imports app lazily and builds a bare
PetCompanion via object.__new__, so it never runs the heavy VoiceCompanion init
or touches audio/GL.
"""
import importlib
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Stub the external LLM SDKs (no network / no real client) so the real
# core.model / she / core.recording / body_tags load whether we run standalone
# or after test_headless.py (which stubs the same modules for the session).
import types as _types


def _sdk_module(name, **attrs):
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


for _m in ("core", "core.config", "core.model", "core.memory"):
    sys.modules.pop(_m, None)
_sdk_module("cerebras")
_sdk_module("cerebras.cloud")
_sdk_module("cerebras.cloud.sdk", Cerebras=type("Cerebras", (), {}))
_sdk_module("ollama", chat=lambda *a, **k: None, list=lambda *a, **k: [])
_sdk_module("dotenv", load_dotenv=lambda *a, **k: None)

from behavior_state import BehaviorState, BehaviorSnapshot, FeelingsBucket  # noqa: E402
from body_tags import (_DEFAULT_ACTIONS, BodyActions, resolve_mood)  # noqa: E402
from core.config import _DEFAULTS as CONFIG_DEFAULTS  # noqa: E402
from core.model import parse_mood_tag  # noqa: E402

_PROJECT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3a — mood vocabulary consistency
# ─────────────────────────────────────────────────────────────────────────────

def test_mood_love_resolves_through_shared_path():
    """[mood:love] drives the LLM mood tag AND the body action identically."""
    # LLM/tag path (core.model.parse_mood_tag)
    mood, rest = parse_mood_tag("[mood:love] I adore you")
    assert mood == "love"
    assert rest == "I adore you"
    # body path (body_tags) — same canonical name
    assert resolve_mood("mood:love") == "love"
    # and bare form is identical
    mood, _ = parse_mood_tag("[love] Hi")
    assert mood == "love"


def test_mood_idle_resolves_but_uses_body_fallback():
    """[mood:idle] resolves for the body/sprite/TTS path but is never offered
    to the model (excluded from the LLM mood list)."""
    mood, rest = parse_mood_tag("[mood:idle] ...")
    assert mood == "idle"
    assert rest == "..."
    assert resolve_mood("mood:idle") == "idle"


def test_idle_not_in_llm_mood_list_but_love_is():
    """core.config derives its LLM-offered moods from body_actions.json, so idle
    (which needs a sprite fallback) is excluded while love is present."""
    moods = CONFIG_DEFAULTS["moods"]
    assert "idle" not in moods
    assert "love" in moods
    assert "aroused" in moods


def test_config_moods_match_body_actions_single_source():
    """body_actions.json is the single source of truth: the config list is
    exactly the enabled body moods minus idle."""
    ba = BodyActions(data=_DEFAULT_ACTIONS)
    body_moods = [m for m, cfg in ba.moods.items() if cfg.get("enabled", True)]
    # reset the cached config default to re-read from the file if possible
    expected = [m for m in body_moods if m != "idle"]
    assert set(CONFIG_DEFAULTS["moods"]) == set(expected)
    assert "idle" not in CONFIG_DEFAULTS["moods"]


def test_live2d_moods_already_has_idle_love_aroused():
    """live2d_moods.json must already carry idle/love/aroused — do NOT add
    duplicates; verify the file is the source of those sprite moods."""
    p = _PROJECT / "config" / "live2d_moods.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for name in ("idle", "love", "aroused"):
        assert name in data, name


def test_idle_falls_back_with_no_sprite():
    """'idle' has no dedicated LLM mood entry yet still resolves through the
    shared resolver (the fallback path that drives sprite/TTS/body)."""
    ba = BodyActions(data=_DEFAULT_ACTIONS)
    # idle is resolvable even though it is not offered to the model
    assert ba.resolve_mood("idle") == "idle"
    # and it produces a zero impulse in the behavior state (no arousal shift)
    s = BehaviorState(rng=random.Random(0))
    v0, a0 = s.valence, s.arousal
    s.mood_impulse("idle")
    assert (s.valence, s.arousal) == (v0, a0)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3b — FeelingsBucket (pure)
# ─────────────────────────────────────────────────────────────────────────────

def _snap(valence=0.0, arousal=0.5, engagement=0.5):
    return BehaviorSnapshot(valence=valence, arousal=arousal, engagement=engagement)


def test_feelings_phrase_all_nine_buckets():
    """Every (valence, arousal) bucket maps to a short self-feeling phrase."""
    # a FRESH bucket per cell so happy/sad/calm buckets are not blurred by
    # hysteresis state carried over from a previous cell
    phrases = set()
    for val in (-0.5, 0.0, 0.5):
        for aro in (0.2, 0.5, 0.8):
            phrases.add(FeelingsBucket().phrase(_snap(valence=val, arousal=aro)))
    # nine distinct combinations produce non-empty, non-whitespace phrases
    assert len(phrases) == 9
    for p in phrases:
        assert p and not p.isspace()


def test_feelings_phrase_never_mentions_user_behaviour():
    """The phrase must describe only HER own feeling — never the user's
    behaviour, never ignored/wandered/away (the pet can't know or complain)."""
    forbidden = ("ignored", "wander", "away", "alone", "left", "user")
    for val in (-0.5, 0.0, 0.5):
        for aro in (0.2, 0.5, 0.8):
            # fresh bucket per cell keeps each starting from neutral
            p = FeelingsBucket().phrase(_snap(valence=val, arousal=aro))
            low = p.lower()
            for word in forbidden:
                assert word not in low, (word, p)


def test_feelings_hysteresis_holds_then_flips():
    """A bucket holds near a boundary and only flips once it crosses the outer
    threshold; once flipped it holds until it crosses back past the release
    threshold (no turn-to-turn flapping)."""
    fb = FeelingsBucket()
    # start calm (valence 0.0). nudge toward happy below the enter threshold
    # (0.22) — stays calm.
    p_calm = fb.phrase(_snap(valence=0.10))
    assert "calm" in p_calm and "cheerful" not in p_calm
    # cross the enter threshold (0.22) -> flips to cheerful
    p_happy = fb.phrase(_snap(valence=0.3))
    assert "cheerful" in p_happy
    # drop back below the enter threshold but ABOVE the release threshold
    # (0.05, i.e. the release-happy bound) -> still holds cheerful
    p_hold = fb.phrase(_snap(valence=0.10))
    assert "cheerful" in p_hold
    # cross back past the release threshold (<= -0.05) -> releases to calm
    p_release = fb.phrase(_snap(valence=-0.1))
    assert "cheerful" not in p_release


def test_snapshot_is_immutable_against_source():
    """BehaviorState.snapshot() is a frozen copy: mutating the live object must
    not change an already-published snapshot."""
    s = BehaviorState(rng=random.Random(3))
    s.mood_impulse("sad")                 # dvalence -0.8 -> valence -0.8
    snap = s.snapshot()
    assert snap.valence == -0.8
    assert snap.arousal < 0.5              # sad darousal -0.4
    # mutate the live state heavily AFTER the snapshot is published
    s.mood_impulse("joyful")
    s.mood_impulse("excited")
    s.interact()
    # snapshot still holds the earlier frozen values
    assert snap.valence == -0.8
    assert snap.arousal < 0.5
    # and it is a frozen dataclass (cannot be mutated in place)
    import dataclasses
    assert dataclasses.is_dataclass(snap)
    with pytest.raises(AttributeError):
        setattr(snap, "valence", 0.9)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3b — PetCompanion._state_line gating
# ─────────────────────────────────────────────────────────────────────────────

def _load_app():
    app = importlib.import_module("app")
    return app


def _bare_companion(app, getter, feelings=None):
    """Build a PetCompanion WITHOUT running its heavy __init__."""
    pc = object.__new__(app.PetCompanion)
    pc._feelings = feelings if feelings is not None else FeelingsBucket()
    pc._state_getter = getter
    return pc


def test_state_line_off_by_default(monkeypatch):
    """state_in_prompt defaults to OFF so no state line is injected."""
    app = _load_app()
    monkeypatch.setattr(app, "_STATE_IN_PROMPT", False)
    # body gate satisfied but state_in_prompt off -> no line
    monkeypatch.setattr(app, "_FACE_RENDERER", "live2d")
    monkeypatch.setattr(app, "_LIVE2D_AVAILABLE", True)
    monkeypatch.setattr(app, "_BODY_CONTROL", True)
    pc = _bare_companion(app, lambda: _snap(valence=0.5, arousal=0.8))
    assert app.PetCompanion._state_line(pc) is None


def test_state_line_gated_by_body_rule(monkeypatch):
    """The BODY gate (renderer + live2d available + body_control) must hold in
    addition to state_in_prompt."""
    app = _load_app()
    monkeypatch.setattr(app, "_STATE_IN_PROMPT", True)
    # one gate member false -> no line regardless of renderer settings
    monkeypatch.setattr(app, "_FACE_RENDERER", "sprites")  # not live2d
    monkeypatch.setattr(app, "_LIVE2D_AVAILABLE", True)
    monkeypatch.setattr(app, "_BODY_CONTROL", True)
    pc = _bare_companion(app, lambda: _snap(valence=0.5))
    assert app.PetCompanion._state_line(pc) is None


def test_state_line_returns_none_without_getter(monkeypatch):
    app = _load_app()
    monkeypatch.setattr(app, "_STATE_IN_PROMPT", True)
    monkeypatch.setattr(app, "_FACE_RENDERER", "live2d")
    monkeypatch.setattr(app, "_LIVE2D_AVAILABLE", True)
    monkeypatch.setattr(app, "_BODY_CONTROL", True)
    pc = _bare_companion(app, None)           # no snapshot publisher wired
    assert app.PetCompanion._state_line(pc) is None


def test_state_line_emits_phrase_and_instruction(monkeypatch):
    app = _load_app()
    monkeypatch.setattr(app, "_STATE_IN_PROMPT", True)
    monkeypatch.setattr(app, "_FACE_RENDERER", "live2d")
    monkeypatch.setattr(app, "_LIVE2D_AVAILABLE", True)
    monkeypatch.setattr(app, "_BODY_CONTROL", True)
    pc = _bare_companion(app, lambda: _snap(valence=0.5, arousal=0.8))
    line = app.PetCompanion._state_line(pc)
    assert line is not None
    # phrase embedded
    assert "cheerful and full of energy" in line
    # the do-not-mention instruction is present
    assert "Do not mention or complain" in line
    assert "being ignored" in line
    assert "how long the user was away" in line


def test_state_line_use_snapshot_not_live_state(monkeypatch):
    """The line is built from the immutable snapshot passed by the getter, never
    from the mutable BehaviorState.  Uses a stale snapshot to prove it."""
    app = _load_app()
    monkeypatch.setattr(app, "_STATE_IN_PROMPT", True)
    monkeypatch.setattr(app, "_FACE_RENDERER", "live2d")
    monkeypatch.setattr(app, "_LIVE2D_AVAILABLE", True)
    monkeypatch.setattr(app, "_BODY_CONTROL", True)
    stale = _snap(valence=0.5, arousal=0.8)   # cheerful
    pc = _bare_companion(app, lambda: stale)
    line = app.PetCompanion._state_line(pc)
    assert "cheerful" in line


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3b — loop-stability with a stubborn LLM
# ─────────────────────────────────────────────────────────────────────────────

def test_loop_stability_bounded_not_pinned():
    """A stub LLM that ALWAYS tags whatever mood the state currently suggests
    must not drive valence/arousal to an extreme over 50 turns: the half-life
    decay between turns keeps the system bounded (never pinned to ±1.0)."""
    cfg = json.loads((_PROJECT / "config" / "behavior_state.json")
                     .read_text(encoding="utf-8"))
    s = BehaviorState(rng=random.Random(1), config=cfg, closeness=0.0)
    fb = FeelingsBucket()
    val_vals = []
    aro_vals = []
    for _ in range(50):
        snap = s.snapshot()
        m = fb.suggested_mood(snap)      # the "LLM" echoes the suggested mood
        s.mood_impulse(m)
        s.update(60.0)                    # ~1 minute between turns (decay)
        val_vals.append(s.valence)
        aro_vals.append(s.arousal)
    assert -1.0 <= min(val_vals) <= max(val_vals) <= 1.0
    assert 0.0 <= min(aro_vals) <= max(aro_vals) <= 1.0
    # not pinned at an extreme — a robust margin from the clamps
    assert max(val_vals) < 0.99
    assert min(val_vals) > -0.99
    assert max(aro_vals) < 0.99
    # the system actually moves (a range exists), i.e. it is alive not frozen
    assert max(val_vals) - min(val_vals) > 0.01
