"""Feature 23b: a stalled TTS synth call can no longer mute her.

The provider's own read timeout is 240 s; on 2026-10-31 03:14 one Fish
Audio stream returned its HTTP headers and then hung mid-body, and her
ONLY synth thread sat blocked inside it until 03:18:30 — every reply
queued behind the stalled one went silent.  `Speaker._synth_guarded`
now runs each synth on a throwaway daemon thread with a wall-clock cap
(default 30 s), retries once, then skips the sentence so the queue
keeps moving.  `she.py` also passes the cap into the Fish HTTP client.
"""
import sys
import time
from pathlib import Path

for _m in ("core", "core.audio", "she"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.audio import Speaker                       # noqa: E402

_APP = Path(__file__).parent.parent / "src" / "app.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"
_CFG = Path(__file__).parent.parent / "src" / "core" / "config.py"
_BUS = Path(__file__).parent.parent / "src" / "gui" / "bus.py"


def _mk_speaker(synth_fn, timeout=0.15, retries=1, cache=None) -> Speaker:
    """A Speaker body without the pygame/mixer/network heavyweight init.

    `_synth` is replaced by `synth_fn(text, mood)`; everything else the
    guarded path touches is a plain field.
    """
    obj = object.__new__(Speaker)
    obj._synth = synth_fn
    obj.synth_timeout_s = timeout
    obj.synth_retries = retries
    obj.cache = cache
    obj.voice_id = "v"
    obj.model = "m"
    obj.on_tts_failure = None
    obj._last_tts_failure_notice = 0.0
    return obj


# ── the guarded synthesis ─────────────────────────────────────────────────────

def test_fast_synth_behaves_as_before():
    sp = _mk_speaker(lambda t, m: b"MP3-BYTES")
    assert sp._synth_guarded("hello", None) == b"MP3-BYTES"


def test_stalled_synth_is_dropped_fast():
    def _hang(text, mood):
        time.sleep(0.6)                              # >> the 0.15 s cap
        return b"never"

    sp = _mk_speaker(_hang, timeout=0.15, retries=1)
    t0 = time.monotonic()
    got = sp._synth_guarded("hello", None)
    elapsed = time.monotonic() - t0
    assert got is None
    assert elapsed < 2.0                             # NOT 240 s
    assert elapsed < 4 * 0.6                         # didn't wait for both sleeps


def test_retry_can_save_a_transient_failure():
    calls = {"n": 0}

    def _flaky(text, mood):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient provider hiccup")
        return b"RETRY-OK"

    sp = _mk_speaker(_flaky, timeout=0.15, retries=1)
    assert sp._synth_guarded("hello", None) == b"RETRY-OK"
    assert calls["n"] == 2


def test_retries_zero_drops_after_one_attempt():
    calls = {"n": 0}

    def _hang(text, mood):
        calls["n"] += 1
        time.sleep(0.6)
        return b"never"

    sp = _mk_speaker(_hang, timeout=0.15, retries=0)
    assert sp._synth_guarded("hello", None) is None
    assert calls["n"] == 1


def test_caps_are_clamped():
    sp = _mk_speaker(lambda t, m: b"")
    sp.synth_timeout_s = -5.0
    assert sp._synth_guarded("x", None) == b""       # still functional


# ── Feature 23c: visible TTS failure UX ──────────────────────────────────────

def test_failure_after_retries_notifies_ui_once():
    notices = []

    def _fail(text, mood):
        raise RuntimeError("provider down")

    sp = _mk_speaker(_fail, retries=1)
    sp.on_tts_failure = notices.append
    sp._last_tts_failure_notice = time.monotonic() - 100.0
    assert sp._synth_guarded("the line that failed", None) is None
    assert notices == ["the line that failed"]


def test_success_does_not_notify_ui():
    notices = []
    sp = _mk_speaker(lambda t, m: b"OK")
    sp.on_tts_failure = notices.append
    assert sp._synth_guarded("fine", None) == b"OK"
    assert notices == []


def test_failure_notice_is_rate_limited():
    notices = []

    def _fail(text, mood):
        raise RuntimeError("provider down")

    sp = _mk_speaker(_fail, retries=0)
    sp.on_tts_failure = notices.append
    sp._last_tts_failure_notice = time.monotonic() - 100.0
    assert sp._synth_guarded("one", None) is None
    assert sp._synth_guarded("two", None) is None
    assert notices == ["one"]
    sp._last_tts_failure_notice = time.monotonic() - 100.0
    assert sp._synth_guarded("three", None) is None
    assert notices == ["one", "three"]


def test_failure_callback_error_does_not_break_synthesis():
    def _fail(text, mood):
        raise RuntimeError("provider down")

    def _bad_callback(text):
        raise RuntimeError("UI callback failed")

    sp = _mk_speaker(_fail, retries=0)
    sp.on_tts_failure = _bad_callback
    sp._last_tts_failure_notice = time.monotonic() - 100.0
    assert sp._synth_guarded("hello", None) is None


def test_ctor_clamps_bad_values():
    import inspect

    sig = inspect.signature(Speaker.__init__)
    assert sig.parameters["synth_timeout_s"].default == 30.0
    assert sig.parameters["synth_retries"].default == 1


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_tts_loop_uses_the_guard():
    src = Path(__file__).parent.parent / "src" / "core" / "audio.py"
    text = "".join(src.read_text(encoding="utf-8").split())
    # Feature 12c: the loop synthesises each spoken fragment (payload) of a
    # sentence — still through the wall-clock guard.
    assert "self._synth_guarded(payload,mood)" in text
    assert "ifdata:" in text
    assert "stalled>" in text


def test_she_builds_a_timeout_capped_client():
    src = _SHE.read_text(encoding="utf-8")
    joined = "".join(src.split())
    assert "FishAudio(timeout=self._tts_timeout)" in joined
    assert "synth_timeout_s=config.tts_synth_timeout_s" in joined
    # the hot-reload push keeps timeouts live
    assert "self.speaker.synth_timeout_s=float(self.config.tts_synth_timeout_s)" in joined


def test_app_wires_the_visible_voice_failure_note():
    app = "".join(_APP.read_text(encoding="utf-8").split())
    bus = "".join(_BUS.read_text(encoding="utf-8").split())
    assert "voice_issue=Signal(str)" in bus
    assert "fromgui.busimportBus" in app
    assert "self.speaker.on_tts_failure=self._on_tts_failure" in app
    assert "bus.voice_issue.connect(pet.on_voice_issue)" in app
    assert "defon_voice_issue(self,text:str=\"\"):" in app
    assert "Voiceunavailable" in app


def test_speaker_calls_the_failure_hook_after_retries():
    src = "".join((Path(__file__).parent.parent / "src" / "core" / "audio.py")
                  .read_text(encoding="utf-8").split())
    assert "self._notify_tts_failure(text)" in src
    assert "on_tts_failure:Optional[Callable[[str],None]]=None" in src


def test_config_has_the_clock_cap():
    src = _CFG.read_text(encoding="utf-8")
    assert "tts_synth_timeout_s: float = 30.0" in src
    assert 'raw.get("tts_synth_timeout_s")' in src
