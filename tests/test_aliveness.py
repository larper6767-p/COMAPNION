"""Feature 27: aliveness layer (turn texture, listening, canon guard).

The layer must never edit personality.json — it is feature-owned prompt text
in `config/aliveness.yaml` plus a tiny loader module.  These tests pin the
loader's behavior, the closeness-tier merge, and the wiring tripwires.
Pure tests only: no Qt, no LLM, no audio.
"""
import json
import sys
from pathlib import Path

import pytest

# test_headless.py stubs core.* at import time; drop poisoned entries first.
for _m in ("core", "core.config", "core.aliveness"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import aliveness                                            # noqa: E402
from core.config import Config                                        # noqa: E402

ROOT = Path(__file__).parent.parent


def _function_source(src: str, marker: str) -> str:
    """Slice one method out of a source file (from its def to the next def)."""
    start = src.index(marker)
    nxt = src.find("\n    def ", start + len(marker))
    if nxt == -1:
        src.find("\ndef ", start + len(marker))
        nxt = src.find("\ndef ", start + len(marker))
    return src[start:nxt if nxt != -1 else len(src)]


# ── loader ────────────────────────────────────────────────────────────────────

def test_live_config_loads_enabled_with_all_sections():
    c = aliveness.load_config()
    assert c["enabled"] is True
    assert c["daily_summary"] is True
    for section in ("turn_shape", "listening", "in_character_fallback",
                    "opinions", "examples", "proactive_instruction",
                    "question_guard"):
        assert c.get(section, "").strip(), section


def test_missing_file_means_disabled(tmp_path):
    c = aliveness.load_config(tmp_path / "missing.yaml")
    assert c["enabled"] is False
    assert aliveness.prompt_block(c) == ""
    assert aliveness.guard_block("you okay?", c) == ""
    assert aliveness.proactive_instruction(c) == ""
    assert aliveness.closeness_tiers_extra(c) == {}


def test_malformed_file_means_disabled(tmp_path):
    p = tmp_path / "aliveness.yaml"
    p.write_text("enabled: [unclosed\n: :", encoding="utf-8")
    c = aliveness.load_config(p, force=True)
    assert c["enabled"] is False


def test_disabled_yaml_turns_everything_off(tmp_path):
    p = tmp_path / "aliveness.yaml"
    p.write_text(
        'enabled: false\nturn_shape: "nope"\ncloseness_tiers_extra:\n'
        '  "150": settled\n', encoding="utf-8")
    c = aliveness.load_config(p, force=True)
    assert aliveness.prompt_block(c) == ""
    assert aliveness.closeness_tiers_extra(c) == {}


def test_prompt_block_joins_sections_in_order(tmp_path):
    p = tmp_path / "aliveness.yaml"
    p.write_text(
        'enabled: true\nturn_shape: "A"\nlistening: "B"\n'
        'in_character_fallback: ""\nopinions: "C"\nexamples: "D"\n'
        'question_guard: "G"\n', encoding="utf-8")
    c = aliveness.load_config(p, force=True)
    assert aliveness.prompt_block(c) == "A\n\nB\n\nC\n\nD"


# ── closeness tiers ───────────────────────────────────────────────────────────

def test_extra_tiers_are_validated(tmp_path):
    p = tmp_path / "aliveness.yaml"
    p.write_text(
        'enabled: true\ncloseness_tiers_extra:\n'
        '  "150": settled\n'
        '  "banana": ignored\n'
        '  "-5": ignored\n'
        '  "201": ignored\n'
        '  "200": ""\n', encoding="utf-8")
    c = aliveness.load_config(p, force=True)
    assert aliveness.closeness_tiers_extra(c) == {"150": "settled"}


@pytest.fixture
def tiny_config(tmp_path):
    (tmp_path / "personality.json").write_text(json.dumps({
        "name": "Test", "system_prompt": "x", "greeting": "hi",
        "temperature": 0.7, "max_reply_length": 100,
        "closeness_tiers": {"0": "wary", "100": "bonded"},
    }), encoding="utf-8")
    (tmp_path / "aliveness.yaml").write_text(
        'enabled: true\n'
        'closeness_tiers_extra:\n'
        '  "150": settled live\n'
        '  "200": built a life\n', encoding="utf-8")
    return tmp_path


def test_config_merges_extras_from_its_own_config_dir(tiny_config):
    cfg = Config(tiny_config)
    assert cfg.closeness_hint(200) == "built a life"
    assert cfg.closeness_hint(150) == "settled live"
    # persona-owned tiers below the extras are untouched
    assert cfg.closeness_hint(100) == "bonded"
    assert cfg.closeness_hint(0) == "wary"


def test_real_config_has_the_settled_tiers():
    cfg = Config()
    assert "built a life" in cfg.closeness_tiers.get("200", "")
    assert "settled" in cfg.closeness_tiers.get("150", "")
    assert "built a life" in cfg.closeness_hint(200)
    # at 200 she must NOT be given the honeymoon tier anymore
    assert cfg.closeness_hint(200) != cfg.closeness_hint(100)


# ── question guard ────────────────────────────────────────────────────────────

def test_ends_with_question_variants():
    assert aliveness.ends_with_question("you okay?")
    assert aliveness.ends_with_question('Smiles. "What do you want to do?" ')
    assert not aliveness.ends_with_question("Come here.")
    assert not aliveness.ends_with_question("Why? Because I said so. ")
    assert not aliveness.ends_with_question("")


def test_live_guard_only_fires_after_a_question():
    assert aliveness.guard_block("Okay, what did they say?")
    assert aliveness.guard_block("Come here. I'm not repeating myself.") == ""


def test_guard_ignores_trailing_narration_and_memory_notes():
    assert aliveness.guard_block("Are you okay? **she tilts her head**")
    assert aliveness.guard_block(
        "Are you okay? {{remember: the user seemed tired}}")
    assert aliveness.guard_block(
        "Are you okay? **tilt** {{remember: the user seemed tired}}")


def test_guard_does_not_fire_for_markup_only_questions():
    assert aliveness.guard_block("Come here. **Is that okay?**") == ""
    assert aliveness.guard_block(
        "Come here. {{remember: should I ask?}}") == ""
    assert aliveness.guard_block(
        "Are you okay? **she waits** No, really.") == ""


# ── wiring tripwires ──────────────────────────────────────────────────────────

def _she_src() -> str:
    return (ROOT / "src" / "she.py").read_text(encoding="utf-8")


def _app_src() -> str:
    return (ROOT / "src" / "app.py").read_text(encoding="utf-8")


def test_she_appends_the_aliveness_prompt_block():
    fn = _function_source(_she_src(), "def system_message")
    assert "_alive.prompt_block()" in fn


def test_pet_appends_the_question_guard_after_the_state_line():
    src = _app_src()
    anchor = 'logger.info("[body] state -> prompt: %s", line)'
    assert anchor in src
    assert src.index(anchor) < src.index("_alive.guard_block(last_reply)")


def test_generate_proactive_uses_the_real_persona_not_a_generic_card():
    fn = _function_source(_app_src(), "def generate_proactive")
    assert "self.system_message(query=None)" in fn
    assert "companion who notices" not in fn
    assert "Memory/relationship context" not in fn


def test_generate_proactive_asks_a_due_thread_before_generating():
    fn = _function_source(_app_src(), "def generate_proactive")
    grounded = fn.index("grounded = self._thread_followup()")
    generated = fn.index("self.system_message(query=None)")
    assert grounded < generated


def test_say_proactive_parses_and_emits_mood():
    fn = _function_source(_app_src(), "def say_proactive")
    assert "parse_mood_tag(message)" in fn
    assert "bus.mood.emit(mood)" in fn
    assert "bus.proact.emit(display)" in fn


def test_shutdown_writes_a_fresh_session_summary():
    src = _she_src()
    fn = _function_source(src, "def _summarize_session")
    assert 'cfg.get("daily_summary")' in fn
    assert "save_summary" in fn
    assert "_summarize_session()" in _function_source(src, "def shutdown")


# ── canon guards (Marin cleanup must stay cleaned) ────────────────────────────

def test_retired_marin_summaries_are_gone():
    path = ROOT / "config" / "episodes" / "summaries.json"
    if not path.exists():
        pytest.skip("live episode summaries are not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "Marin" not in text


def test_live_memory_has_no_marin_fact_junk():
    path = ROOT / "config" / "memory.json"
    if not path.exists():
        pytest.skip("live memory is not present in this checkout")
    facts = json.loads(path.read_text(encoding="utf-8"))
    joined = " ".join(facts).lower()
    for bad in ("marin", "the assistant", "sexy cosplays",
                "search the internet", "i have chicken", "almost 2",
                "roleplay"):
        assert bad not in joined, bad


def test_vector_collection_is_evelyn_not_marin():
    text = (ROOT / "config" / "memory.yaml").read_text(encoding="utf-8")
    assert 'collection: "evelyn_memories"' in text
    assert 'collection: "marin_memories"' not in text


def test_user_visible_config_text_uses_evelyn():
    for rel in ("config/text_input.yaml", "config/vision.yaml",
                "config/emotions.yaml", "config/memory.yaml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "Marin" not in text, rel

    src = _app_src()
    for old in ("Type to Marin", "for Marin to work",
                "fires Marin's", "[MARIN]"):
        assert old not in src, old
    assert "Type to Evelyn" in src


def test_fallback_defaults_use_evelyn():
    config_src = (ROOT / "src" / "core" / "config.py").read_text(
        encoding="utf-8")
    assert '"wake_word": "hello eve"' in config_src
    vector_src = (ROOT / "src" / "memory" / "vector_store.py").read_text(
        encoding="utf-8")
    assert '"collection": "evelyn_memories"' in vector_src
    input_src = (ROOT / "src" / "core" / "text_input.py").read_text(
        encoding="utf-8")
    assert "to Evelyn" in input_src
