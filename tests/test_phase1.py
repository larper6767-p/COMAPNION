"""Phase 1a tests: shared mood resolution + streaming tag parser.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_phase1.py -v

These cover only pure parsing/resolution logic in body_tags (and the shared
parse_mood_tag delegate).  No GL context / QApplication / model required.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Importing core.model drags in the LLM SDKs.  test_headless stubs them for the
# whole session; we ensure importable stubs exist here too so the REAL
# core.model / body_tags load whether we run standalone or after test_headless.
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
_sdk_module("cerebras.cloud.sdk", Cerebras=type("Cerebras", (), {}) )
_sdk_module("ollama", chat=lambda *a, **k: None, list=lambda *a, **k: [])
_sdk_module("dotenv", load_dotenv=lambda *a, **k: None)

from body_tags import (  # noqa: E402
    BodyActions, StreamingTagParser, extract_tags, resolve_mood, _DEFAULT_ACTIONS,
)
from core.model import parse_mood_tag  # noqa: E402


@pytest.fixture()
def ba() -> BodyActions:
    return BodyActions(data=_DEFAULT_ACTIONS)


# ── 1. Shared mood resolution (bare / [mood:NAME] / alias) ─────────────────────

def test_shared_resolution_real_names(ba):
    for name in ("idle", "joyful", "excited", "embarrassed", "sad", "angry",
                 "surprised", "curious", "relaxed", "love", "aroused"):
        assert resolve_mood(name) == name, name


def test_resolution_aliases(ba):
    assert resolve_mood("happy") == "joyful"
    assert resolve_mood("shy") == "embarrassed"
    assert resolve_mood("mad") == "angry"
    assert resolve_mood("glad") == "joyful"


def test_resolution_unknown_and_disabled(ba):
    assert resolve_mood("nonsense") is None
    assert resolve_mood("") is None


# ── 2. parse_mood_tag delegates to the SAME resolver ───────────────────────────

def test_parse_mood_tag_bare(ba):
    mood, rest = parse_mood_tag("[joyful] Oh my gosh!")
    assert mood == "joyful"
    assert rest == "Oh my gosh!"


def test_parse_mood_tag_explicit(ba):
    mood, rest = parse_mood_tag("[mood:joyful] Oh my gosh!")
    assert mood == "joyful"
    assert rest == "Oh my gosh!"


def test_parse_mood_tag_alias(ba):
    mood, rest = parse_mood_tag("[mood:happy] Oh my gosh!")
    assert mood == "joyful"
    assert rest == "Oh my gosh!"


def test_parse_mood_tag_unknown_not_stripped(ba):
    mood, rest = parse_mood_tag("[nonsense] Hi!")
    assert mood is None
    assert rest == "[nonsense] Hi!"


def test_parse_mood_tag_no_leading_tag(ba):
    mood, rest = parse_mood_tag("Just a normal sentence.")
    assert mood is None
    assert rest == "Just a normal sentence."


# ── 3. extract_tags: clean text + honoured tags + cap (mood uncapped) ─────────

def test_extract_tags_strips_and_strips_leftover(ba):
    clean, tags, cap = extract_tags("[mood:joyful] [fx:blush] Oh! Hello there", ba)
    assert "[" not in clean and "]" not in clean
    assert clean == "Oh! Hello there"
    kinds = [t.kind for t in tags]
    assert kinds == ["mood", "fx"]


def test_extract_last_resort_leftover_filter(ba):
    # A non-tag bracket containing ':' that the parser didn't recognise still gets
    # stripped before TTS (requirement: a missed tag is never spoken).
    clean, tags, cap = extract_tags("Let's count 1:2 and [thing:value] ok", ba)
    assert "[thing:value]" not in clean
    assert "1:2" in clean  # colons not inside brackets are preserved


def test_extract_strips_clear(ba):
    clean, tags, cap = extract_tags("[fx:blush] [clear] Go", ba)
    assert "clear" not in clean and "[" not in clean
    assert "clear" in [t.kind for t in tags]


def test_cap_counts_only_non_mood(ba):
    # 4 fx/look/gesture/wink count toward the cap; mood tags do not.
    cap0 = ba.max_tags_per_reply  # 4
    text = ("[mood:joyful] [fx:blush] [fx:tears] [look:left] [look:right] "
            "[gesture:nod] " + "some " * 30)
    clean, tags, cap = extract_tags(text, ba)
    capped = [t for t in tags if t.kind in ("fx", "look", "gesture", "wink")]
    # Only the cap's worth of capped tags survive; mood is untouched.
    assert len(capped) == cap0
    assert cap == 0
    moods = [t for t in tags if t.kind == "mood"]
    assert len(moods) == 1 and moods[0].name == "joyful"


def test_extract_unknown_tag_dropped(ba):
    clean, tags, cap = extract_tags("[unknown:thing] [mood:joyful] Hi", ba)
    # unknown kind dropped silently; its bracket removed
    assert "unknown" not in clean
    assert [t.kind for t in tags] == ["mood"]


# ── 4. Streaming parser: hold-back / split-chunks (requirement 5) ─────────────

def test_streaming_tag_split_across_chunks(ba):
    p = StreamingTagParser(ba)
    # tag arrives split: "[mo" then "od:joyful] Hi!"
    assert p.feed("[mo") == []
    assert p.feed("od:joyful] Hi!") == []
    frames = p.feed(" How are you? Fine.")
    assert len(frames) >= 1
    # First frame has the resolved mood and clean text, no bracket leaked.
    assert frames[0].mood == "joyful"
    assert "[" not in frames[0].text and "]" not in frames[0].text


def test_streaming_completed_sentence_goes_immediately(ba):
    p = StreamingTagParser(ba)
    frames = p.feed("Hello! ")
    assert len(frames) == 1
    assert frames[0].text == "Hello!"
    # Second sentence after a later tag
    frames2 = p.feed("[mood:sad] I'm tired. ")
    assert len(frames2) == 1
    assert frames2[0].mood == "sad"
    assert frames2[0].text == "I'm tired."


def test_streaming_unclosed_bracket_flushed_after_30(ba):
    p = StreamingTagParser(ba)
    # "[" that never closes within 40 chars of the terminator is NOT held
    # forever: it flushes as plain text once the sentence terminates.
    frames = p.feed("[" + "x" * 45 + "! ")
    assert len(frames) == 1
    assert frames[0].text.endswith("!")


def test_bare_bracket_number_ignored(ba):
    # "[1]" is not a usable action tag -> produces no honoured tag and no crash.
    clean, tags, cap = extract_tags("See [1] and [mood:joyful] go", ba)
    assert [t.kind for t in tags] == ["mood"]
    assert not any("[1]" == t.raw for t in tags)


# ── 5. Tag after punctuation attaches to NEXT sentence (requirement 2) ─────────

def test_tag_after_punctuation_goes_to_next_sentence(ba):
    p = StreamingTagParser(ba)
    p.feed("[mood:joyful] First. ")              # tag at very start (leading)
    assert p.current_mood == "joyful"
    # tag placed AFTER sentence punctuation -> next sentence
    frames = p.feed("Second! [mood:sad] Third. ")
    # 'Second!' emits; the [mood:sad] follows punctuation so goes to 'Third.'
    second = [f for f in frames if f.text == "Second!"]
    third = [f for f in frames if f.text == "Third."]
    assert second and second[0].mood == "joyful"   # unchanged by the later tag
    assert third and third[0].mood == "sad"        # picked up by the later tag


def test_tag_only_fragment_never_sent_to_tts(ba):
    p = StreamingTagParser(ba)
    # leading tag-only fragment with no words -> stream holds (no terminator yet)
    frames = p.feed("[mood:joyful] ")
    assert frames == []
    # the tag applies once the first real sentence arrives
    frames = p.feed("Hello there! ")
    assert len(frames) == 1
    assert frames[0].text == "Hello there!"
    assert frames[0].mood == "joyful"


def test_empty_after_strip_skips_tts(ba):
    p = StreamingTagParser(ba)
    frames = p.feed("[mood:joyful] ")
    frames2 = p.feed("")
    assert frames == []
    assert frames2 == []
    # finish() with nothing real also emits nothing
    assert p.finish() == []


def test_trailing_tag_fires_at_last_sentence_start(ba):
    p = StreamingTagParser(ba)
    frames1 = p.feed("Hello there. ")          # emitted now, mood=None
    assert frames1 and frames1[0].text == "Hello there."
    # a trailing tag with no following sentence
    frames2 = p.feed("[mood:sad]")
    assert frames2 == []                       # not emitted alone
    final = p.finish()
    # per our chosen rule, the trailing [mood:sad] fires at the START of the
    # last emitted sentence (update the last frame's mood).
    assert final == []                         # no new sentence in finish()
    assert frames1[0].mood == "sad"           # pending tag applied to last frame


# ── 6. Mood semantics: leading default + running mood (requirement 3) ─────────

def test_mood_running_semantics(ba):
    p = StreamingTagParser(ba)
    frames = p.feed("[mood:joyful] One. Two. ")
    # both One. and Two. inherit the leading joyful default
    one = [f for f in frames if f.text == "One."]
    two = [f for f in frames if f.text == "Two."]
    assert one and one[0].mood == "joyful"
    assert two and two[0].mood == "joyful"
    frames = p.feed("[mood:sad] Three. ")
    three = [f for f in frames if f.text == "Three."]
    assert three and three[0].mood == "sad"


def test_caps_enforced_across_reply(ba):
    p = StreamingTagParser(ba)
    # Exceed the 4 non-mood cap across sentences; mood unaffected.
    frames = p.feed("[fx:blush] [fx:tears] [look:left] "
                    "[look:right] [gesture:nod] A. ")
    # only the first 4 capped survive across the reply
    first = frames[0]
    capped = [t for t in first.tags if t.kind in ("fx", "look", "gesture", "wink")]
    assert len(capped) == ba.max_tags_per_reply
    frames2 = p.feed("[gesture:shake] B. ")
    b = frames2[0]
    b_capped = [t for t in b.tags if t.kind in ("fx", "look", "gesture", "wink")]
    assert b_capped == []          # cap exhausted -> silently dropped


# ── 7. Robustness: case/spacing, punctuation-adjacent, malformed brackets ──────

def test_uppercase_and_spaced_tags_are_honoured(ba):
    # Tag kind and pool names are matched case-insensitively, and spaces around
    # the value are trimmed, so "[FX: Blush]" works like "[fx:blush]".
    clean, tags, cap = extract_tags("[FX: Blush] Oh! [GESTURE: Nod] hi.", ba)
    kinds = [(t.kind, t.name) for t in tags]
    assert ("fx", "blush") in kinds
    assert ("gesture", "nod") in kinds
    assert "[" not in clean and "]" not in clean


def test_uppercase_mood_and_alias(ba):
    clean, tags, cap = extract_tags("[MOOD: Happy] Hello!", ba)
    mood_tags = [(t.kind, t.name) for t in tags if t.kind == "mood"]
    assert mood_tags == [("mood", "joyful")]   # alias + case-normalised


def test_tag_right_before_punctuation(ba):
    # A tag placed immediately before the sentence terminator (no space before
    # the '!') is still parsed out, and the sentence still ends cleanly.
    p = StreamingTagParser(ba)
    frames = p.feed("[mood:surprised] No way[fx:blush]! ")
    assert frames and frames[0].text == "No way!"
    assert frames[0].mood == "surprised"
    assert any(t.kind == "fx" for t in frames[0].tags)
    assert "[" not in frames[0].text and "]" not in frames[0].text


def test_unclosed_bracket_at_end_dropped_not_spoken(ba):
    # A reply that ends with an unclosed "[fx:bl" must not leak to TTS: it is
    # held (no ']'), then on finish() the leftover filter drops the tag prefix.
    p = StreamingTagParser(ba)
    frames = p.feed("Hey there. ")
    assert frames and frames[0].text == "Hey there."
    p.feed("[fx:bl")                       # unclosed tag-prefix mid/finish
    final = p.finish()
    # The leftover filter strips the unclosed prefix so it is never spoken.
    texts = [f.text for f in final]
    assert not any("[" in t for t in texts)
    assert not any("fx:bl" in t for t in texts)


def test_more_than_four_tags_capped(ba):
    # More than 4 non-mood (capped) tags in one reply: only the first
    # max_tags_per_reply survive; the rest are silently dropped.
    text = ("[fx:blush] [fx:tears] [look:left] [look:right] "
            "[gesture:nod] [gesture:shake] Go!")
    clean, tags, cap = extract_tags(text, ba)
    capped = [t for t in tags if t.kind in ("fx", "look", "gesture", "wink")]
    assert len(capped) == ba.max_tags_per_reply  # 4
    assert cap == 0


def test_plain_brackets_and_index_survive(ba):
    # Ordinary text brackets like "list[0]" and "[1]" are NOT tags and are NOT
    # stripped by the last-resort filter (no colon, or not lowercase word:word).
    clean, tags, cap = extract_tags("Use list[0] and [1] please.", ba)
    assert "list[0]" in clean and "[1]" in clean
    assert tags == []


def test_note_bracket_not_stripped_by_leftover_filter(ba):
    # A genuine "[Note: something]" (capital + space) must survive the last-
    # resort filter, which only strips short lowercase word:word brackets.
    clean, tags, cap = extract_tags("He said [Note: something] earlier.", ba)
    assert "[Note: something]" in clean
    assert tags == []


def test_lowercase_word_word_leftover_still_stripped(ba):
    # A short lowercase "word:word" bracket that the parser didn't recognise as
    # any known tag is still removed before TTS (a missed tag is never spoken).
    clean, tags, cap = extract_tags("Weird [thing:value] token here.", ba)
    assert "[thing:value]" not in clean
    assert tags == []


# ── 8. Wink sides: [wink], [wink:left], [wink:right] (decision 1) ──────────────

def _wink_tag(text, ba):
    """Return the honoured wink Tag (or None) parsed from ``text``."""
    _, tags, _ = extract_tags(text, ba)
    for t in tags:
        if t.kind == "wink":
            return t
    return None


def test_wink_bare_uses_default_side(ba):
    t = _wink_tag("[wink] hello!", ba)
    assert t is not None
    assert t.kind == "wink"
    assert t.name == ba.wink_default_side()      # "right" by default


def test_wink_explicit_left_and_right(ba):
    assert _wink_tag("[wink:left] hi", ba).name == "left"
    assert _wink_tag("[wink:right] hi", ba).name == "right"


def test_wink_unknown_side_falls_back_to_default(ba):
    # An unknown side is not dropped - it falls back to the default side.
    t = _wink_tag("[wink:bogus] hi", ba)
    assert t is not None and t.name == ba.wink_default_side()


def test_wink_gesture_tag_is_a_wink(ba):
    # [gesture:wink] is shorthand for a wink with the default side.
    t = _wink_tag("[gesture:wink] hi", ba)
    assert t is not None and t.kind == "wink"
    assert t.name == ba.wink_default_side()


def test_wink_counts_toward_cap(ba):
    # wink is a capped (non-mood) tag like fx/look/gesture.
    cap0 = ba.max_tags_per_reply
    text = ("[wink:left] [wink:right] [fx:blush] [look:left] "
            "[gesture:nod] Go!")
    clean, tags, cap = extract_tags(text, ba)
    capped = [t for t in tags if t.kind in ("fx", "look", "gesture", "wink")]
    assert len(capped) == cap0
    assert cap == 0


def test_wink_not_stripped_by_leftover_filter(ba):
    # A wink tag is fully stripped (never spoken), not left for the leftover filter.
    clean, tags, cap = extract_tags("[wink:left] said hello.", ba)
    assert "[wink:left]" not in clean and "wink" not in clean


def test_enabled_capped_actions_include_wink_sides(ba):
    acts = ba.enabled_capped_actions()
    assert "wink:left" in acts and acts["wink:left"]["kind"] == "wink"
    assert "wink:right" in acts and acts["wink:right"]["kind"] == "wink"
    assert "wink" in acts and acts["wink"]["kind"] == "wink"
    # the bare 'wink' entry uses the default side
    assert acts["wink"]["side"] == ba.wink_default_side()
