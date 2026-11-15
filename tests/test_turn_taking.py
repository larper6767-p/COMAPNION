"""Features 21+22: patient turn-taking + her listening noises (pure policy).

core/turn_taking.py is policy-only (endpointing decision, backchannel
gating, tail slicing).  The real capture/recorder changes are asserted as
static tripwires, and the recorder's judge contract is exercised with the
`pause_judge` argument contract documented in core/recording.py.
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import turn_taking as tt                      # noqa: E402

_APP = Path(__file__).parent.parent / "src" / "app.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"

NOW = datetime(2026, 11, 6, 10, 0)


# ── ends_open ─────────────────────────────────────────────────────────────────

def test_finished_punctuation_is_closed():
    assert tt.ends_open("so we went home.") is False
    assert tt.ends_open("did you really?") is False
    assert tt.ends_open("yeah.") is False
    assert tt.ends_open("and then I got the job!") is False


def test_hanging_punctuation_is_open():
    assert tt.ends_open("we went home,") is True
    assert tt.ends_open("and then—") is True
    assert tt.ends_open("so wait,") is True


def test_last_word_decides():
    assert tt.ends_open("so we drove to the") is True
    assert tt.ends_open("and then um") is True
    assert tt.ends_open("we drove home") is False
    assert tt.ends_open("I had a") is True            # 'a' tails open


def test_empty_partial_is_open():
    assert tt.ends_open("") is True
    assert tt.ends_open("   ") is True


def test_custom_open_words_from_config():
    cfg = dict(tt.DEFAULTS)
    cfg["open_words"] = ["bruh"]
    assert tt.ends_open("I saw him bruh", cfg) is True
    assert tt.ends_open("I saw him", cfg) is False


# ── decide: the endpoint gate ─────────────────────────────────────────────────

def _cfg(**over):
    cfg = dict(tt.DEFAULTS)
    cfg.update(over)
    return cfg


def test_complete_phrase_dispatches_at_end_silence():
    dispatch, why = tt.decide("so we went home.", 1.7, _cfg())
    assert dispatch is True


def test_complete_phrase_waits_before_end_silence():
    dispatch, why = tt.decide("so we went home.", 1.2, _cfg())
    assert dispatch is False
    assert "keep waiting" in why


def test_open_tail_gets_the_full_budget():
    dispatch, _ = tt.decide("and then we went to the", 1.9, _cfg())
    assert dispatch is False                       # needs 1.6 + 3.0
    dispatch, _ = tt.decide("and then we went to the", 4.6, _cfg())
    assert dispatch is True


def test_barely_anything_gets_min_words_patience():
    dispatch, _ = tt.decide("yeah", 1.7, _cfg())   # 1 word < min_words=2
    assert dispatch is False
    dispatch, _ = tt.decide("yeah", 3.2, _cfg())   # 1.6 + 1.5 + margin
    assert dispatch is True


def test_empty_partial_never_dispatches_immediately():
    dispatch, _ = tt.decide("", 1.7, _cfg())
    assert dispatch is False


def test_config_off_values_still_sane():
    dispatch, _ = tt.decide("hello there again.", 0.0, _cfg(end_silence_s=2.0))
    assert dispatch is False


# ── backchannels: her listening noises ────────────────────────────────────────

def test_backchannel_window_and_rules():
    cfg = _cfg()
    line = tt.pick_line(speech_s=10.0, sil_s=1.1, now_s=100.0,
                        last_bc_s=None, cfg=cfg)
    assert line in cfg["backchannel_lines"]


def test_backchannel_disabled_means_silent():
    cfg = _cfg(backchannels=False)
    assert tt.pick_line(10.0, 1.1, 100.0, None, cfg) is None


def test_backchannel_outside_silence_window():
    cfg = _cfg()
    assert tt.pick_line(10.0, 0.3, 100.0, None, cfg) is None    # still talking
    assert tt.pick_line(10.0, 1.7, 100.0, None, cfg) is None    # about to end


def test_backchannel_needs_min_speech():
    cfg = _cfg()
    assert tt.pick_line(2.0, 1.1, 100.0, None, cfg) is None


def test_backchannel_gap_enforced():
    cfg = _cfg()
    assert tt.pick_line(10.0, 1.1, 105.0, 100.0, cfg) is None   # 5 s < gap 9
    line = tt.pick_line(10.0, 1.1, 109.1, 100.0, cfg)
    assert line is not None                                     # 9.1 s >= gap


def test_backchannel_from_config_bank_only():
    cfg = _cfg(backchannel_lines=["Mhm.", "Yeah…"])
    got = {tt.pick_line(12.0, 1.2, 50.0 + i * 10, None, cfg)
           for i in range(8)}
    assert got <= {"Mhm.", "Yeah…"}


def test_backchannel_empty_bank_is_silent():
    cfg = _cfg(backchannel_lines=[])
    assert tt.pick_line(12.0, 1.1, 100.0, None, cfg) is None


# ── tail_seconds: cheap partial transcribe window ─────────────────────────────

def test_tail_seconds_window_math():
    sr = 1000                                       # tiny scale for the test
    audio = np.arange(sr * 10, dtype=np.float32)     # 10 s at 1 kHz
    tail = tt.tail_seconds(audio, 2.0, sr)
    assert len(tail) == 2000
    assert tail[0] == float(sr * 10 - 2000)          # the LAST 2 s only


def test_tail_seconds_handles_empty_audio():
    assert len(tt.tail_seconds(None, 2.0, 16000)) == 0
    assert len(tt.tail_seconds(np.zeros(0, np.float32), 2.0, 16000)) == 0


def test_tail_seconds_clamps_longer_than_audio():
    audio = np.arange(500, dtype=np.float32)
    assert len(tt.tail_seconds(audio, 9.0, 1000)) == 500


# ── config ────────────────────────────────────────────────────────────────────

def test_defaults_are_off_and_sane():
    assert tt.DEFAULTS["enabled"] is False
    # the noise window sits strictly INSIDE the dispatch threshold
    assert tt.DEFAULTS["backchannel_to_s"] < tt.DEFAULTS["end_silence_s"]


def test_live_config_parses():
    cfg = tt.get_config(force=True)
    assert isinstance(cfg["enabled"], bool)
    assert isinstance(cfg["backchannels"], bool)
    assert float(cfg["end_silence_s"]) > 0.5
    assert isinstance(cfg["open_words"], list)
    assert isinstance(cfg["backchannel_lines"], list) and cfg["backchannel_lines"]


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_she_has_the_record_voice_wrapper():
    src = _SHE.read_text(encoding="utf-8")
    assert "def _record_voice" in src
    assert "def _record_plain" in src
    assert 'from core import turn_taking as _tt' in src
    src_nospace = "".join(src.split())
    assert 'if not cfg.get("enabled", False):' in src
    assert "returnself._record_plain()" in src_nospace


def test_she_judge_transcribes_tail_and_plays_noise():
    src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert "partial=self.transcribe(tail)" in src
    assert "_tt.decide(partial,silence_seconds,cfg)" in src
    assert "_tt.pick_line(" in src
    assert "self.speaker.say(line)" in src
    assert "notself.speaker.is_playing()" in src


def test_she_console_loop_uses_record_voice():
    src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert "audio=self._record_voice()" in src


def test_app_run_turn_uses_record_voice():
    src = "".join(_APP.read_text(encoding="utf-8").split())
    assert "audio=self._record_voice()" in src


def test_recording_loop_owns_the_judge_contract():
    rec = Path(__file__).parent.parent / "src" / "core" / "recording.py"
    src = "".join(rec.read_text(encoding="utf-8").split())
    assert "pause_judge:Optional" in src
    assert 'res.get("dispatch")' in src
    assert "mute_blocks_left" in src
    assert "extreme_silence_s" in src

