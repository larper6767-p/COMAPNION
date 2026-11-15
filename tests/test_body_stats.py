"""Phase 4b tests: body-control statistics (tags per reply, dropped counts,
tag-to-audio-start latency).

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_body_stats.py -v

Three layers are covered:
  1. BodyStats (pure accumulator) — reply/kind/dropped/latency aggregation.
  2. StreamingTagParser.stats() — unknown-dropped + cap-dropped counters that feed
     the [body] reply-stats log line.
  3. Speaker.say() tuple shape — a TAGGED sentence carries t0 (a monotonic stamp)
     while an untagged one carries None.  Built as a bare instance to avoid the
     real TTS threads / pygame.mixer init.
"""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from body_stats import BodyStats  # noqa: E402
from body_tags import (  # noqa: E402
    BodyActions, StreamingTagParser, _DEFAULT_ACTIONS,
)

# NOTE: cache the REAL Speaker class here, at module import time, BEFORE any
# other test (e.g. test_headless.py) replaces sys.modules["core.audio"] with a
# stub.  If that pollution has already happened by the time this module loads,
# we mark the Speaker-latency tests as skippable rather than fail on a fake's
# empty queue.
try:  # noqa: E402
    import core.audio as _core_audio_mod
    _REAL_SPEAKER = _core_audio_mod.Speaker
    _HAS_REAL_SPEAKER = hasattr(_REAL_SPEAKER, "_synth")  # real TTS loop method
except Exception:  # pragma: no cover - env dependent
    _REAL_SPEAKER = None
    _HAS_REAL_SPEAKER = False


@pytest.fixture()
def ba() -> BodyActions:
    return BodyActions(data=_DEFAULT_ACTIONS)


# ── 1. BodyStats (pure accumulator) ───────────────────────────────────────────

class TestBodyStats:
    def test_initial_state(self):
        s = BodyStats()
        assert s.replies == 0 and s.total_tags == 0
        assert s.unknown_dropped == 0 and s.cap_dropped == 0
        assert s.tags_by_kind == {}

    def test_record_reply_counts_replies_and_total(self):
        s = BodyStats()
        s.record_reply({"mood": 1, "fx": 1})     # 2 tags
        s.record_reply({"look": 1})              # 1 tag
        assert s.replies == 2
        assert s.total_tags == 3
        assert s.tags_by_kind == {"mood": 1, "fx": 1, "look": 1}

    def test_record_reply_ignores_empty(self):
        s = BodyStats()
        s.record_reply({})
        s.record_reply({"mood": 0})
        assert s.replies == 0 and s.total_tags == 0

    def test_record_reply_tracks_per_reply_counts(self):
        s = BodyStats()
        s.record_reply({"fx": 1, "look": 1})     # 2 tags this reply
        s.record_reply({"wink": 1, "mood": 1, "fx": 1})  # 3 tags this reply
        # summary uses tags_by_kind + totals; per-reply histogram is intuernal
        sum_ = s.summary()
        assert sum_["replies_with_tags"] == 2
        assert sum_["total_tags"] == 5

    def test_unknown_and_cap_dropped(self):
        s = BodyStats()
        s.record_unknown_dropped(2)
        s.record_cap_dropped(1)
        assert s.unknown_dropped == 2 and s.cap_dropped == 1
        # non-positive increments are ignored
        s.record_unknown_dropped(0)
        s.record_cap_dropped(-3)
        assert s.unknown_dropped == 2 and s.cap_dropped == 1

    def test_latency_aggregation(self):
        s = BodyStats()
        s.record_latency(0.4)
        s.record_latency(0.2)
        s.record_latency(0.7)
        lat = s.summary()["tag_to_audio_latency_ms"]
        assert lat["samples"] == 3
        assert lat["min"] == pytest.approx(0.2)
        assert lat["max"] == pytest.approx(0.7)
        assert lat["avg"] == pytest.approx((0.4 + 0.2 + 0.7) / 3)
        assert lat["last"] == pytest.approx(0.7)

    def test_latency_ignores_none_and_negative(self):
        s = BodyStats()
        s.record_latency(None)
        s.record_latency(-0.5)
        assert s.summary()["tag_to_audio_latency_ms"]["samples"] == 0

    def test_latency_summary_empty(self):
        s = BodyStats()
        lat = s.summary()["tag_to_audio_latency_ms"]
        assert lat == {"samples": 0, "last": 0.0, "min": 0.0, "avg": 0.0, "max": 0.0}

    def test_summary_shape(self):
        s = BodyStats()
        s.record_reply({"mood": 1})
        s.record_unknown_dropped(1)
        s.record_latency(0.3)
        sum_ = s.summary()
        assert set(sum_) == {"replies_with_tags", "total_tags", "tags_by_kind",
                             "unknown_dropped", "cap_dropped",
                             "tag_to_audio_latency_ms"}

    def test_reset(self):
        s = BodyStats()
        s.record_reply({"fx": 1})
        s.record_latency(0.1)
        s.record_unknown_dropped(2)
        s.reset()
        assert s.replies == 0 and s.total_tags == 0 and s.unknown_dropped == 0
        assert s.summary()["tag_to_audio_latency_ms"]["samples"] == 0


# ── 2. StreamingTagParser.stats() — unknown / cap-dropped counters ────────────

class TestParserStats:
    def test_counts_unknown_dropped(self, ba):
        p = StreamingTagParser(ba)
        # "[thing:value]" is unknown (kind not recognised) -> counted unknown and
        # never honoured; the mood tag is honoured and drives the frame.
        frames = p.feed("[thing:value] [mood:joyful] Oh hi! ")
        st = p.stats()
        assert st["unknown_dropped"] == 1
        assert st["cap_dropped"] == 0
        assert frames and frames[0].mood == "joyful"
        assert "thing" not in frames[0].text

    def test_counts_cap_dropped(self, ba):
        p = StreamingTagParser(ba)
        # 5 capped (non-mood) tags but cap is 4 -> 1 dropped, 4 honoured.
        text = ("[fx:blush] [fx:tears] [look:left] [look:right] [gesture:nod] "
                "Go! ")
        frames = p.feed(text)
        capped = [t for t in frames[0].tags if t.kind in ("fx", "look", "gesture", "wink")]
        assert len(capped) == ba.max_tags_per_reply   # 4 honoured
        st = p.stats()
        assert st["cap_dropped"] == 1
        assert st["unknown_dropped"] == 0

    def test_no_stats_for_clean_reply(self, ba):
        p = StreamingTagParser(ba)
        p.feed("Just a perfectly normal sentence. ")
        st = p.stats()
        assert st["unknown_dropped"] == 0 and st["cap_dropped"] == 0

    def test_mood_not_counted_unknown_or_cap(self, ba):
        p = StreamingTagParser(ba)
        # All moods honoured and uncapped -> no drops.
        p.feed("[mood:joyful] [mood:sad] [mood:love] Oh. ")
        st = p.stats()
        assert st["unknown_dropped"] == 0 and st["cap_dropped"] == 0

    def test_prose_brackets_not_unknown(self, ba):
        p = StreamingTagParser(ba)
        # Capital, spaced and index brackets are NOT "unknown tags dropped".
        p.feed("He said [Note: something] and list[0] and [1]. Fine. ")
        st = p.stats()
        assert st["unknown_dropped"] == 0

    def test_stats_accumulates_across_feed_and_finish(self, ba):
        p = StreamingTagParser(ba)
        p.feed("[thing:value] First. ")          # 1 unknown
        p.feed("[other:value] Second. ")         # 1 more unknown
        st = p.stats()
        assert st["unknown_dropped"] == 2


# ── 3. Speaker.say() latency stamp ────────────────────────────────────────────

def _bare_speaker():
    """A Speaker with exactly the state ``say()`` touches (no real TTS/threads).

    Uses the REAL Speaker class captured at import time.  If a pre-imported stub
    has replaced it, the test is skipped (the real class isn't available).
    """
    if not _HAS_REAL_SPEAKER:
        pytest.skip("core.audio.Speaker was stubbed by another test (test_headless)")
    spk = _REAL_SPEAKER.__new__(_REAL_SPEAKER)
    spk.enabled = True
    spk.gen = 0
    spk.sentences = queue.Queue()
    return spk


class TestSpeakerSayLatency:
    def test_tagged_sentence_carries_t0(self):
        spk = _bare_speaker()
        spk.say("Hello there!", mood="joyful", tags=[[1, "fx", "blush", "[fx:blush]"]])
        # Feature 12c: the sentence tuple carries its part list; plain text
        # is a single say-part with no cues.
        gen, reveal, parts, mood, tags, t0 = spk.sentences.get_nowait()
        assert tags                      # tags preserved
        assert parts == [("say", "Hello there!")]
        assert isinstance(t0, float)
        # t0 is a recent monotonic stamp
        assert 0 <= time.monotonic() - t0 < 0.1

    def test_untagged_sentence_has_none_t0(self):
        spk = _bare_speaker()
        spk.say("No tags here.")
        gen, reveal, parts, mood, tags, t0 = spk.sentences.get_nowait()
        assert tags == []
        assert parts == [("say", "No tags here.")]
        assert t0 is None

    def test_marker_text_triggers_sfx_payload(self):
        """Feature 12c (legacy path here): a marked sentence queues its SFX
        cue as a part and the symbol is stripped from the speech text."""
        spk = _bare_speaker()
        spk.say("Don't stop — nnh~")
        gen, reveal, parts, mood, tags, t0 = spk.sentences.get_nowait()
        assert parts[0][0] == "cue"
        hits = parts[0][1]
        assert hits and hits[0][0] == "~"           # breath category queued
        assert parts[1][0] == "say"
        assert "~" not in reveal                    # cleaned for TTS as before

    def test_empty_text_not_queued(self):
        spk = _bare_speaker()
        spk.say("   ")
        assert spk.sentences.empty()

    def test_disabled_not_queued(self):
        spk = _bare_speaker()
        spk.enabled = False
        spk.say("Anything!", tags=[[1, "fx", "blush", "[fx:blush]"]])
        assert spk.sentences.empty()
