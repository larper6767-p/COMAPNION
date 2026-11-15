"""Feature 6 tests: emotion-style detection (scene mood).

Pure logic — no Qt, no LLM, no network.  Verifies the port of the JS hook:
safety-first ordering, hybrids, negation, word boundaries, antonym suppression,
the top-3 cap, and the wiring into ``VoiceCompanion.system_message``.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emotion.style_detector import (                                 # noqa: E402
    DEFAULT_CONFIG_PATH,
    Style,
    StyleConfig,
    build_scene_block,
    detect,
    load_config,
    normalize,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def categories(text, cfg=None):
    return [m.category for m in detect(text, cfg)]


@pytest.fixture(scope="module")
def cfg():
    c = load_config(DEFAULT_CONFIG_PATH)
    assert c.enabled, "shipped config should be enabled"
    return c


# ── normalisation / boundaries ───────────────────────────────────────────────

def test_normalize_strips_punctuation_and_apostrophes():
    assert normalize("I'm HAPPY!!!") == " im happy "


def test_word_boundary_does_not_match_substrings(cfg):
    assert "Angry" not in categories("I made a cake", cfg)       # mad
    assert "Sad" not in categories("I bought a saddle", cfg)     # sad
    assert "Joyful" not in categories("the greatest trick", cfg) # great


def test_empty_input_is_neutral(cfg):
    assert categories("", cfg) == []
    assert categories(None, cfg) == []
    assert categories("   ", cfg) == []


# ── primary emotions ─────────────────────────────────────────────────────────

def test_primary_joy(cfg):
    assert "Joyful" in categories("this is amazing", cfg)


def test_primary_sad_and_angry(cfg):
    assert "Sad" in categories("I feel so lonely and empty", cfg)
    assert "Angry" in categories("I am so furious right now", cfg)


def test_sarcasm_outranks_joy(cfg):
    got = categories("yeah right, thanks for nothing", cfg)
    assert got[0] == "Sarcastic"


# ── negation + idioms ────────────────────────────────────────────────────────

def test_negation_suppresses_emotion(cfg):
    assert "Joyful" not in categories("I'm not happy", cfg)
    assert "Sad" not in categories("I'm not sad", cfg)
    assert "Angry" not in categories("I'm not angry", cfg)


def test_negation_window_is_short(cfg):
    # A negator far away from the keyword must not suppress it.
    assert "Angry" in categories("I am not tired of saying this: I am angry", cfg)


def test_positive_idiom_beats_negation(cfg):
    assert "Joyful" in categories("I couldn't be happier", cfg)
    assert "Joyful" in categories("honestly, not bad at all", cfg)


# ── hybrids ──────────────────────────────────────────────────────────────────

def test_hybrid_requires_both(cfg):
    assert categories("I'm sad and calm", cfg) == ["Melancholy"]
    assert "Melancholy" not in categories("I'm sad", cfg)


def test_hybrid_alt_phrase(cfg):
    assert "Bittersweet" in categories("happy and sad", cfg)


def test_hybrids_take_precedence_over_primaries(cfg):
    got = categories("I'm anxious and confused", cfg)
    assert got == ["Anxious"]


# ── safety ───────────────────────────────────────────────────────────────────

def test_safety_always_wins(cfg):
    got = categories("I'm furious, please stop", cfg)
    assert got == ["Boundary"]


def test_safety_phrases(cfg):
    for text in ("leave me alone", "back off", "that's too far", "I'm uncomfortable"):
        assert categories(text, cfg) == ["Boundary"], text


def test_safety_has_no_false_positives(cfg):
    # "don't want to" is deliberately NOT a boundary — it is ordinary speech.
    assert categories("I don't want to go to work", cfg) != ["Boundary"]
    assert categories("I can't stop thinking about it", cfg) != ["Boundary"]


def test_boundary_block_tells_her_to_halt(cfg):
    block = build_scene_block("please stop", cfg)
    assert "Boundary" in block
    assert "Halt the current thread" in block


# ── selection ────────────────────────────────────────────────────────────────

def test_antonyms_are_suppressed(cfg):
    got = categories("I'm angry and calm", cfg)
    assert "Angry" in got
    assert "Calm" not in got


def test_top_three_cap(cfg):
    text = "I'm happy, sad, angry, scared, confused and bored"
    assert len(categories(text, cfg)) <= 3


def test_duplicate_categories_are_removed(cfg):
    got = categories("I'm happy and joyful and delighted", cfg)
    assert got.count("Joyful") == 1


# ── output block ─────────────────────────────────────────────────────────────

def test_block_format(cfg):
    block = build_scene_block("this is amazing", cfg)
    assert "## Scene mood" in block
    assert "The mood of the scene is:" in block
    assert "#1 Joyful:" in block
    assert "do not mention this analysis" in block.lower()


def test_neutral_appends_nothing_by_default(cfg):
    assert build_scene_block("the weather is fine", cfg) == ""


def test_append_neutral_option(cfg):
    c = replace(cfg, append_neutral=True)
    block = build_scene_block("the weather is fine", c)
    assert "#1 Neutral" in block


def test_disabled_returns_nothing(cfg):
    c = replace(cfg, enabled=False)
    assert detect("I am furious", c) == []
    assert build_scene_block("please stop", c) == ""


# ── config robustness ────────────────────────────────────────────────────────

def test_malformed_config_disables_feature(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = load_config(p)
    assert c.enabled is False
    assert build_scene_block("please stop", c) == ""


def test_missing_config_disables_feature(tmp_path):
    c = load_config(tmp_path / "nope.json")
    assert c.enabled is False


def test_config_skips_invalid_styles(tmp_path):
    p = tmp_path / "styles.json"
    p.write_text(json.dumps({
        "enabled": True,
        "emotions": [
            {"category": "Broken"},                       # missing fields
            {"category": "Fine", "keywords": ["fine"],
             "personality": "fine", "scenario": "ok", "priority": 4},
        ],
    }), encoding="utf-8")
    c = load_config(p)
    assert [s.category for s in c.emotions] == ["Fine"]
    assert categories("I'm fine", c) == ["Fine"]


def test_custom_style_via_dataclass():
    c = StyleConfig(
        enabled=True,
        emotions=(
            Style("Testy", ("testy",), "testy", "testing", 4),
        ),
    )
    assert categories("so testy", c) == ["Testy"]


# ── wiring into the real prompt ──────────────────────────────────────────────

def _system_message(query):
    # test_headless installs a stub ``she`` (VoiceCompanion = object) and stub
    # ``core.*`` modules.  Drop them so this test exercises the real code.
    for _name in list(sys.modules):
        if _name == "she" or _name == "core" or _name.startswith("core."):
            sys.modules.pop(_name, None)

    from core.config import Config
    from she import VoiceCompanion

    cfg = Config()
    memory = type("M", (), {
        "build_context_block": lambda self, max_facts=0, query=None: "",
    })()
    obj = object.__new__(VoiceCompanion)
    obj.config = cfg
    obj.memory = memory
    obj.last_exchange = None
    return VoiceCompanion.system_message(obj, query=query)["content"]


def test_system_message_includes_scene_block():
    msg = _system_message("I'm furious, please stop")
    assert "## Scene mood" in msg
    assert "Boundary" in msg


def test_system_message_omits_scene_block_when_neutral():
    msg = _system_message("the weather is fine")
    assert "## Scene mood" not in msg


def test_system_message_without_query_is_unchanged():
    msg = _system_message(None)
    assert "## Scene mood" not in msg
