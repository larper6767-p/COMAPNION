"""Feature 30 tests: the cadence measurement tool (tools/measure_cadence.py).

Pure functions only - no episode files needed except via tmp_path.
"""
import json
import sys
from pathlib import Path


# test_headless.py stubs core.* at import time; drop poisoned entries.
for _m in ("core", "core.narration", "core.memory_notes",
           "core.memory_hygiene", "measure_cadence"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from measure_cadence import analyze, load_days, summarize    # noqa: E402


def test_analyze_counts_ending_and_contained_questions():
    msgs = [
        "So what do you want to do?",
        'Mirror stares. "Change the subject, please?" ',
        "Come here. No, don't talk yet.",
        "How how what why?",
        "I said good evening.",           # contains none
        "Yes? Then stop talking about it.",   # contains, not ending
    ]
    d = analyze(msgs)
    assert d["count"] == 6
    assert d["ends_with_question"] == 3
    assert d["ends_with_question_pct"] == round(100.0 * 3 / 6, 1)
    assert d["contains_question"] == 4
    assert d["contains_question_pct"] == round(100.0 * 4 / 6, 1)
    assert d["avg_chars"] > 0
    # two consecutive messages ending in questions
    assert d["question_pairs"] == 1


def test_analyze_empty():
    d = analyze([])
    assert d["count"] == 0 and d["ends_with_question_pct"] == 0.0


def test_analyze_ignores_silent_narration_and_private_notes():
    msgs = [
        "**she tilts her head** Are you coming?",
        "I'm fine. {{remember: Does he like tea?}}",
        "**wink**",
        "**tilt** Tell me what happened.",
        "It's late. **she smiles**",
    ]
    d = analyze(msgs)
    assert d["count"] == 5
    assert d["ends_with_question"] == 1
    assert d["contains_question"] == 1
    assert d["narration_msgs"] == 4
    assert d["memory_note_msgs"] == 1
    assert d["one_word_narration_spans"] == 2   # "why?" is not in this set; tilt/wink are
    # The note's question mark must not create a fake consecutive question pair.
    assert d["question_pairs"] == 0


def test_analyze_question_pairs_use_spoken_text_only():
    d = analyze([
        "**beat** Are you there?",
        "**beat** Ready?",
        "**beat** No question here.",
    ])
    assert d["question_pairs"] == 1


def test_analyze_strips_one_word_labels_from_speech():
    d = analyze(["**wink**", "**tilt** Fine."])
    assert d["ends_with_question"] == 0
    assert d["contains_question"] == 0
    assert d["one_word_narration_spans"] == 2
    assert d["avg_chars"] == 2


def test_load_days_from_tmp_episodes(tmp_path):
    ep = tmp_path / "episodes"
    ep.mkdir()
    (ep / "2026-10-30.json").write_text(json.dumps([
        {"role": "assistant", "text": "Are you still there?"},
        {"role": "user", "text": "yes"},
        {"role": "assistant", "text": "Good. Then tell me what happened."},
    ]), encoding="utf-8")
    (ep / "junk.json").write_text("not a date", encoding="utf-8")
    days = load_days(ep)
    assert list(days) == ["2026-10-30"]
    assert days["2026-10-30"]["count"] == 2


def test_summarize_pools_weighted_by_count():
    days = {
        "d1": {"count": 4, "ends_with_question": 4, "ends_with_question_pct": 100.0,
               "contains_question": 4, "contains_question_pct": 100.0, "avg_chars": 100},
        "d2": {"count": 1, "ends_with_question": 0, "ends_with_question_pct": 0.0,
               "contains_question": 0, "contains_question_pct": 0.0, "avg_chars": 100},
    }
    t = summarize(days)
    assert t["count"] == 5
    assert t["ends_with_question_pct"] == 80.0


def test_summarize_empty():
    assert summarize({})["count"] == 0
