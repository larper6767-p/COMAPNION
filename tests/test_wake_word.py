"""Wake-word matcher tests (Phase 1 item 2).

The old Whisper fallback accepted any substring and matched when only half of
a multi-word phrase was present.  These tests pin the stricter whole-phrase
behaviour and the exact false positives seen in the logs.
"""
from pathlib import Path
import sys

# test_headless.py installs stub core/sounddevice modules at import time.
# Drop them so this module imports the real matcher in a full-suite run.
for _name in list(sys.modules):
    if _name == "core" or _name.startswith("core.") or _name == "sounddevice":
        sys.modules.pop(_name, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.recording import wake_word_matches


def test_accepts_whole_phrase_with_punctuation_and_spacing():
    for text in (
        "hello eve",
        "Hello, Eve!",
        "well hello eve",
        "hello-eve",
        "  hello   eve  ",
    ):
        assert wake_word_matches(text, "hello eve"), text


def test_rejects_partial_phrase_and_substring_hits():
    for text in (
        "evelyn",
        "hello",
        "eve",
        "hello evie",
        "hello evening",
        "hey brother",
        "they eat",
        "everything",
        "i know in hong kong everyone warned her",
    ):
        assert not wake_word_matches(text, "hello eve"), text


def test_requires_a_contiguous_phrase():
    # Both words are present, but the phrase was not actually said.
    assert not wake_word_matches("hello there eve", "hello eve")


def test_single_word_phrase_needs_a_word_boundary():
    assert wake_word_matches("Computer, wake up.", "computer")
    assert not wake_word_matches("computerized voice", "computer")


def test_empty_or_punctuation_only_never_matches():
    assert not wake_word_matches("hello", "")
    assert not wake_word_matches("", "hello eve")
    assert not wake_word_matches("hello eve", "...")


def test_whisper_loop_uses_the_whole_phrase_matcher():
    src = (Path(__file__).parent.parent / "src" / "core" / "recording.py").read_text(
        encoding="utf-8"
    )
    assert "wake_word_matches(text, self.wake_word)" in src
    assert "hits >= max" not in src
