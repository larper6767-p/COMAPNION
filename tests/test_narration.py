"""Feature 28 tests: silent narration (display-only stage lines).

Pure module tests + wiring tripwires.  The runtime contract: narration spans
(**...**) never reach the TTS queue; they reach the bubble display instead;
the plain text (no narration) keeps word order; stray asterisks can never be
voiced since stripping is unconditional.
"""
import sys
from pathlib import Path


for _m in ("core", "core.narration", "core.aliveness"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import aliveness                                            # noqa: E402
from core.narration import (                                          # noqa: E402
    extract_narration, is_narration, split_voice, strip_narration,
)

ROOT = Path(__file__).parent.parent


# ── pure core ────────────────────────────────────────────────────────────────

def test_split_voice_separates_span_from_words():
    voice, notes = split_voice("**she steps closer** Where were you.")
    assert voice == "Where were you."
    assert notes == ["**she steps closer**"]


def test_split_voice_multiple_spans_inline():
    voice, notes = split_voice(
        "**she pours wine** then **she circles you** so, talk.")
    assert voice == "then so, talk."
    assert notes == ["**she pours wine**", "**she circles you**"]


def test_split_voice_narration_only_frame():
    voice, notes = split_voice("**she holds his gaze**")
    assert voice == ""
    assert notes == ["**she holds his gaze**"]


def test_split_voice_passes_plain_text_through():
    voice, notes = split_voice("Come here. No, don't talk yet.")
    assert voice == "Come here. No, don't talk yet."
    assert notes == []


def test_split_voice_collapses_whitespace_and_keeps_word_voice():
    voice, notes = split_voice("**a**   spoken    words**b** more")
    assert voice == "spoken words more"
    assert notes == ["**a**", "**b**"]


def test_split_voice_garbage_inputs():
    assert split_voice(None) == ("", [])
    assert split_voice(12) == ("", [])
    assert split_voice(["**x**"]) == ("", [])


def test_extract_narration_display_ready():
    raw = "before **big pause** after  **second beat.**"
    assert extract_narration(raw) == ["**big pause**", "**second beat.**"]
    assert extract_narration(None) == []
    assert extract_narration("no spans") == []


def test_strip_narration_removes_everything_doubled():
    assert strip_narration("**gone** spoken") == "spoken"
    assert strip_narration("clean") == "clean"
    assert strip_narration(None) == ""


def test_is_narration_whole_frame():
    assert is_narration("**she pretends to leave**")
    assert not is_narration("**she** talks")
    assert not is_narration("plain sentence")
    assert not is_narration(None)


# ── config + prompt ──────────────────────────────────────────────────────────

def test_aliveness_has_the_narration_prompt_section():
    c = aliveness.load_config()
    assert "Silent narration" in c["narration"]
    # Prompt-quality follow-up: forbid bare body labels like **tilt**; require
    # a full subject+action beat.  Measured from the live episodes.
    assert "bare body-control label" in c["narration"]
    assert "she tilts her head" in c["narration"]
    assert c["narration_enabled"] is True


def test_loader_defaults_allow_stray_asterisk_safety():
    missing = aliveness.load_config(ROOT / "src" / "nope.yaml")
    assert missing["narration_enabled"] is True


# ── runtime wiring tripwires ─────────────────────────────────────────────────

def _app_src():
    return (ROOT / "src" / "app.py").read_text(encoding="utf-8")


def _she_src():
    return (ROOT / "src" / "she.py").read_text(encoding="utf-8")


def _fn(src, marker):
    start = src.index(marker)
    nxt = src.find("\n    def ", start + len(marker))
    return src[start:nxt if nxt != -1 else len(src)]


def test_body_path_speak_diverts_narration_from_the_voice():
    fn = _fn(_app_src(), "def _speak(text, mood, tags):")
    assert "split_voice(" in fn
    assert "strip_notes(" in fn
    assert "b.text.emit(\" \" + note + \" \")" in fn
    assert "self.speaker.say(voice, mood, tags=tags)" in fn
    # the old full-text speak must be gone
    assert "self.speaker.say(text, mood, tags=tags)" not in fn


def test_legacy_and_cli_paths_use_the_apart_helper():
    assert "self._say_apart(complete, mood)" in _app_src()
    assert "self._say_apart(buf, mood)" in _app_src()
    assert "def _say_apart" in _app_src()
    assert "self._speak_apart(complete, mood)" in _she_src()
    assert "self._speak_apart(buf, mood)" in _she_src()
    assert "def _speak_apart" in _she_src()


def test_proactive_path_splits_narration_too():
    fn = _fn(_app_src(), "def say_proactive")
    assert "split_voice(" in fn
    assert "strip_notes(" in fn
