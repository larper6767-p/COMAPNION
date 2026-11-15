"""Feature 7 tests: sound scripts (intimate-scene onomatopoeia guidance).

Pure logic — no Qt, no LLM, no network.  Covers trigger detection (hard vs
soft/threshold), the linger window, boundary clearing, config robustness, the
TTS symbol strip, and the wiring into ``VoiceCompanion.system_message``.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emotion.sound_script import (                                  # noqa: E402
    DEFAULT_CONFIG_PATH,
    SoundState,
    build_sound_block,
    intimacy_triggered,
    load_config,
    update_state,
)


@pytest.fixture(scope="module")
def cfg():
    c = load_config(DEFAULT_CONFIG_PATH)
    assert c.enabled, "shipped config should be enabled"
    assert c.system_note and c.state_line
    assert c.trigger_patterns and c.context_patterns
    return c


# ── config ───────────────────────────────────────────────────────────────────

def test_config_defaults(cfg):
    assert cfg.linger_turns >= 1
    assert cfg.soft_threshold >= 2


def test_malformed_config_disables(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    c = load_config(p)
    assert c.enabled is False
    assert intimacy_triggered("fuck me", c) is None
    assert build_sound_block(SoundState(1), c) == ""


def test_missing_config_disables(tmp_path):
    assert load_config(tmp_path / "nope.json").enabled is False


def test_config_without_note_disables(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    assert load_config(p).enabled is False


def test_bad_pattern_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "enabled": True,
        "system_note": "note",
        "trigger_patterns": ["[unclosed", "fuck"],
    }), encoding="utf-8")
    c = load_config(p)
    assert len(c.trigger_patterns) == 1
    assert intimacy_triggered("fuck", c) is not None


# ── detection ────────────────────────────────────────────────────────────────

def test_hard_trigger_matches(cfg):
    assert intimacy_triggered("I want you to fuck me", cfg) is not None
    assert intimacy_triggered("I'm moaning already", cfg) is not None
    assert intimacy_triggered("I'm so horny tonight", cfg) is not None
    assert intimacy_triggered("make love to me", cfg) is not None


def test_case_insensitive(cfg):
    assert intimacy_triggered("FUCK ME", cfg) is not None
    assert intimacy_triggered("Moaning", cfg) is not None


def test_neutral_text_does_not_trigger(cfg):
    for text in ("I had a great day", "tell me about the weather",
                 "I bought new pants", "I left my phone on the bed",
                 "please stop, leave me alone", ""):
        assert intimacy_triggered(text, cfg) is None, text


def test_single_soft_word_does_not_trigger(cfg):
    assert intimacy_triggered("kiss", cfg) is None
    assert intimacy_triggered("the door slam", cfg) is None


def test_two_soft_words_trigger(cfg):
    assert intimacy_triggered("kiss me harder", cfg) is not None


def test_disabled_returns_none(cfg):
    assert intimacy_triggered("fuck me", replace(cfg, enabled=False)) is None


# ── state / window ───────────────────────────────────────────────────────────

def test_state_arm_tick_clear():
    s = SoundState()
    assert not s.active
    s.arm(2)
    assert s.active and s.turns_left == 2
    s.tick()
    assert s.turns_left == 1
    s.tick()
    assert not s.active
    s.arm(5)
    s.clear()
    assert not s.active


def test_build_block_only_while_active(cfg):
    s = SoundState()
    assert build_sound_block(s, cfg) == ""
    s.arm(1)
    assert "SYSTEM NOTE" in build_sound_block(s, cfg)
    assert "Currently engaged in intimate activity." in build_sound_block(s, cfg)


def test_update_state_arms_then_expires(cfg):
    s = SoundState()
    # Turn 1 carries the trigger; later turns are ordinary chat.
    present = [bool(update_state(s, "I want you inside me", False, cfg))]
    present += [bool(update_state(s, "I'm still here", False, cfg))
                for _ in range(cfg.linger_turns + 2)]
    assert all(present[:cfg.linger_turns])
    assert not any(present[cfg.linger_turns:])


def test_update_state_boundary_clears(cfg):
    s = SoundState()
    s.arm(4)
    assert update_state(s, "fuck me", True, cfg) == ""
    assert not s.active
    # a boundary suppresses the block even for an intimate message
    assert update_state(s, "fuck me", True, cfg) == ""
    assert not s.active
    # a later intimate message (no boundary) re-arms it
    assert update_state(s, "fuck me", False, cfg) != ""
    assert s.active


def test_update_state_neutral_is_silent(cfg):
    s = SoundState()
    assert update_state(s, "I had a great day", False, cfg) == ""
    assert not s.active


def test_update_state_disabled_clears(cfg):
    s = SoundState()
    s.arm(3)
    assert update_state(s, "fuck me", False, replace(cfg, enabled=False)) == ""
    assert not s.active


# ── TTS symbol strip ─────────────────────────────────────────────────────────

def test_clean_for_speech_drops_notation_symbols():
    _purge_stubs()          # test_headless leaves a stub core.audio behind
    from core.audio import clean_for_speech
    out = clean_for_speech("haahh~ ♥ more ♪ nnah♥")
    assert out == "haahh more nnah"


def test_clean_for_speech_keeps_words_and_marks():
    _purge_stubs()
    from core.audio import clean_for_speech
    out = clean_for_speech('"Don\'t stop," she gasped, "nnh...!"')
    assert "Don't stop" in out
    assert "nnh" in out


# ── wiring into the real prompt ──────────────────────────────────────────────

def _purge_stubs():
    for _name in list(sys.modules):
        if _name == "she" or _name == "core" or _name.startswith("core."):
            sys.modules.pop(_name, None)


def _companion():
    _purge_stubs()
    from core.config import Config
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    obj.config = Config()
    obj.memory = type("M", (), {
        "build_context_block": lambda self, max_facts=0, query=None: "",
    })()
    obj.last_exchange = None
    return VoiceCompanion, obj


def _ask(obj, query):
    return type(obj).system_message(obj, query=query)["content"]


def test_system_message_injects_sound_block():
    VoiceCompanion, obj = _companion()
    msg = _ask(obj, "fuck me")
    assert "SYSTEM NOTE" in msg
    assert "Currently engaged in intimate activity." in msg
    assert "moan" in msg.lower()


def test_system_message_omits_block_when_neutral():
    VoiceCompanion, obj = _companion()
    assert "SYSTEM NOTE" not in _ask(obj, "I had a great day")


def test_system_message_without_query_has_no_block():
    VoiceCompanion, obj = _companion()
    assert "SYSTEM NOTE" not in _ask(obj, None)


def test_block_lingers_then_expires():
    VoiceCompanion, obj = _companion()
    linger = load_config().linger_turns
    queries = ["I'm so horny"] + ["I'm still here"] * (linger + 2)
    seen = [bool("SYSTEM NOTE" in _ask(obj, q)) for q in queries]
    assert all(seen[:linger])
    assert not any(seen[linger:])


def test_boundary_removes_and_blocks_the_sound_block():
    VoiceCompanion, obj = _companion()
    assert "SYSTEM NOTE" in _ask(obj, "fuck me")            # armed
    assert "SYSTEM NOTE" not in _ask(obj, "please stop")    # boundary clears it
    assert not obj._sound_state.active
    # a new intimate message re-arms the window afterwards
    assert "SYSTEM NOTE" in _ask(obj, "fuck me again")
