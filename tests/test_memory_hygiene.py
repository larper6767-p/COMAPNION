"""Feature 27: durable-fact hygiene for the flat memory store.

The old extractor stored whatever the model emitted — ephemeral chatter
("I have chicken"), meta instructions ("search the internet next time"), even
lines the assistant itself had produced.  These tests pin the filter that keeps
only durable user facts.  Pure module: no Qt, no LLM, no file access.
"""
import sys
from pathlib import Path


for _m in ("core", "core.memory_hygiene"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.memory_hygiene import (                                      # noqa: E402
    clean_fact, is_durable_fact, parse_facts,
)

ROOT = Path(__file__).parent.parent


# ── clean_fact ────────────────────────────────────────────────────────────────

def test_clean_fact_normalizes_bullets_quotes_periods_and_case():
    assert clean_fact('"I have chicken."') == "I have chicken"
    assert clean_fact("- the user draws.") == "The user draws"
    assert clean_fact("1. I play a lot of games.") == "I play a lot of games"
    assert clean_fact("   spaced    out   ") == "Spaced out"
    assert clean_fact("no trailing dot") == "No trailing dot"
    assert clean_fact(None) == ""
    assert clean_fact(42) == ""


# ── is_durable_fact ───────────────────────────────────────────────────────────

def test_accepts_durable_third_person_user_facts():
    for fact in (
        "The user draws.",
        "The user lives alone",
        "The user loves J-pop.",
        "The user's favorite album is The Black Parade by My Chemical Romance.",
        "The user is naming a set 'Moon Morning'.",
        "The user lives in Guwahati, Assam.",
    ):
        assert is_durable_fact(fact), fact


def test_rejects_ephemeral_state_and_chatter():
    for fact in (
        "I have chicken.",
        "It's almost 2 a.m. in the morning right now.",
        "I'm planning on making breakfast today.",
        "I'm studying about it currently",
        "I'm gonna need to study it more.",
        "I want to talk about it in a little bit.",
        "My head is getting dizzy right now, so I'll probably head back.",
        "let's do some roleplay",
    ):
        assert not is_durable_fact(fact), fact


def test_rejects_meta_instructions_and_assistant_lines():
    for fact in (
        "Next time, search the internet before answering, especially Genshin.",
        "I thought I'd give you internet access.",
        "The user wants to see the assistant do sexy cosplays.",
        "You look great in that dress.",
    ):
        assert not is_durable_fact(fact), fact


def test_rejects_questions_vague_and_garbage():
    for fact in (
        "What are you doing later?",
        "I like to think.",
        "I feel very close at it",
        "I will stay",
        "I will never be mad at you.",
        "None",
        "I",
        "ok",
        "x" * 250,
        None,
    ):
        assert not is_durable_fact(fact), fact


# ── parse_facts ───────────────────────────────────────────────────────────────

def test_parse_facts_reads_json_and_dedupes_casefold():
    raw = ('```json\n["The user draws.", '
           '"I want to talk about it in a little bit.", "the user draws"]\n```')
    assert parse_facts(raw) == ["The user draws"]


def test_parse_facts_reads_plain_lines_too():
    raw = "- The user plays a lot of games.\n* I'm gonna need to study it more."
    assert parse_facts(raw) == ["The user plays a lot of games"]


def test_parse_facts_reads_object_form():
    raw = '{"facts": ["The user lives in Guwahati, Assam."]}'
    assert parse_facts(raw) == ["The user lives in Guwahati, Assam"]


def test_parse_facts_empty_and_junk_inputs():
    for raw in ("", None, "[]", "none", "None", "no facts.",
                "nothing to add", "I don't know."):
        assert parse_facts(raw) == [], raw


def test_parse_facts_treats_input_lines_separately():
    raw = ("The user draws.\n"
           "Also temporary stuff: I want to talk about it in a little bit.")
    assert parse_facts(raw) == ["The user draws"]


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_she_extract_facts_uses_the_hygiene_filter():
    src = (ROOT / "src" / "she.py").read_text(encoding="utf-8")
    assert "from core.memory_hygiene import clean_fact, parse_facts" in src
    # the old free-form line parser must be gone
    assert "full.splitlines()" not in src
