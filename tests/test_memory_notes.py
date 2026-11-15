"""Feature 29 tests: self-written memory notes ({{remember: ...}}).

Pure module tests + a functional harvest test (companion built with
``object.__new__``, the repo's standard no-hardware pattern) + wiring
tripwires.  Gate on config, hygiene filter shared with auto-extraction.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace


for _m in ("she", "core", "core.memory_notes", "core.memory_hygiene"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import memory_notes as mn                                   # noqa: E402


ROOT = Path(__file__).parent.parent


# ── pure core ────────────────────────────────────────────────────────────────

def test_extract_notes_reads_the_marker():
    raw = ("Come here. {{remember: The user draws daily}} Don't ask why. "
           "{{remember: The user's birthday is Feb 3.}}")
    assert mn.extract_notes(raw) == [
        "The user draws daily", "The user's birthday is Feb 3."]


def test_extract_notes_case_and_space_insensitive():
    raw = "{{Remember: The user skateboards}}  {{rememberXTRACK:y}}"
    assert mn.extract_notes(raw) == ["The user skateboards"]
    assert mn.extract_notes(None) == []


def test_strip_notes_removes_markers_and_whitespace():
    voice, raw = mn.split_note_and_text(
        "okay. {{remember: The user is training for a 10k}} goodnight.")
    assert voice == "okay. goodnight."
    assert raw == ["The user is training for a 10k"]


def test_strip_notes_no_marker_passthrough():
    assert mn.strip_notes("one two") == "one two"
    assert mn.strip_notes("") == ""


def test_hygiene_filter_drops_ephemeral_and_meta():
    raw = [
        "I have chicken.",                        # ephemeral junk
        "Next time search the internet first.",   # meta
        "The user plays cello.",                  # durable ✓
        "THE USER PLAYS CELLO.",                  # duplicate ✓ (deduped)
        "What about later?",                      # question
        "The user wants to see the assistant do sexy cosplays.",  # assistant
    ]
    assert mn.filter_notes(raw) == ["The user plays cello"]


def test_filter_notes_dedupes_against_existing_keys():
    out = mn.filter_notes(
        ["The user plays cello", "The user bikes at dawn"],
        existing_keys={"the user plays cello"})
    assert out == ["The user bikes at dawn"]


# ── functional harvest ───────────────────────────────────────────────────────

def _purge_stubs():
    for _name in list(sys.modules):
        if _name == "she" or _name == "core" or _name.startswith("core."):
            sys.modules.pop(_name, None)


def _companion(tmp_path, self_notes=True, cap=5):
    _purge_stubs()
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    facts: list = []
    holder = {"facts": facts, "saved": []}
    obj.memory = type("M", (), {
        "get_facts": lambda self: list(holder["facts"]),
        "add_fact": lambda self, f: holder["facts"].append(f),
    })()
    cfg_file = tmp_path / "aliveness.yaml"
    cfg_file.write_text(
        f"enabled: true\nself_notes: {json.dumps(self_notes)}\n"
        f"max_notes_per_day: {cap}\n", encoding="utf-8")
    obj.config = SimpleNamespace(
        aliveness_file=cfg_file,
        save_memory=lambda fs: holder["saved"].append(list(fs)),
    )
    return obj, holder


def test_harvest_stores_durable_note_and_saves(tmp_path):
    obj, h = _companion(tmp_path)
    added = obj._harvest_memory_notes(
        "Goodnight. {{remember: The user's workshop is in the basement}}")
    assert added == 1
    assert h["facts"] == ["The user's workshop is in the basement"]
    assert h["saved"] == [["The user's workshop is in the basement"]]


def test_harvest_respects_the_gate_and_the_daily_cap(tmp_path):
    obj, h = _companion(tmp_path, self_notes=True, cap=1)
    assert obj._harvest_memory_notes(
        "hi {{remember: The user breeds orchids}} "
        "{{remember: The user hates raisins}}") == 1
    assert h["facts"] == ["The user breeds orchids"]
    # the daily cap stops any further notes today
    assert obj._harvest_memory_notes(
        "more {{remember: The user races karts on Sundays}}") == 0
    assert len(h["facts"]) == 1


def test_harvest_disabled_by_config(tmp_path):
    obj, h = _companion(tmp_path, self_notes=False)
    assert obj._harvest_memory_notes(
        "x {{remember: The user reads pulp}}") == 0
    assert h["facts"] == []


def test_harvest_dedupes_against_the_fact_card(tmp_path):
    obj, h = _companion(tmp_path)
    h["facts"].append("The user breeds orchids")
    assert obj._harvest_memory_notes(
        "x {{remember: The user breeds orchids}}") == 0


def test_harvest_ignores_junk_notes(tmp_path):
    obj, h = _companion(tmp_path)
    assert obj._harvest_memory_notes(
        "x {{remember: I have chicken}} {{remember: let's do some roleplay}}") == 0
    assert h["facts"] == []


def test_harvest_is_a_silent_noop_without_markers(tmp_path):
    obj, h = _companion(tmp_path)
    assert obj._harvest_memory_notes("plain reply, no note") == 0
    assert h["saved"] == []


# ── wiring tripwires ─────────────────────────────────────────────────────────

def _app_src():
    return (ROOT / "src" / "app.py").read_text(encoding="utf-8")


def _she_src():
    return (ROOT / "src" / "she.py").read_text(encoding="utf-8")


def test_app_harvests_before_passive_extraction():
    src = _app_src()
    harvest = src.index("self._harvest_memory_notes(reply_text)")
    extract = src.index("self._extract_facts(text, reply_text)")
    assert harvest < extract


def test_yaml_gate_is_live_and_cap_sane():
    sys.path.insert(0, str(ROOT / "src"))
    from core import aliveness
    c = aliveness.load_config(force=True)
    assert c["self_notes"] is True
    assert c["max_notes_per_day"] >= 1
    assert "Memory notes" in c["memory_notes"]


def test_yaml_prompt_and_gate_coexist():
    text = (ROOT / "config" / "aliveness.yaml").read_text(encoding="utf-8")
    assert "memory_notes:" in text
    assert "self_notes: true" in text
    assert "max_notes_per_day:" in text
