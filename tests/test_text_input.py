"""Feature 5 tests: text input (type a message instead of speaking).

Pure-logic tests for ``core.text_input`` plus a wiring test proving a typed turn
goes through the SAME ``_respond`` pipeline as a voice turn (so voice behaviour
is unchanged).  No Qt app, no audio hardware, no network.
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

for _m in ("core", "core.text_input"):
    sys.modules.pop(_m, None)

from core.text_input import (                                        # noqa: E402
    TextInputConfig, InlineBubbleState, load_config, normalize_input,
    DEFAULT_CONFIG_PATH, MAX_INPUT_CHARS, MODES,
)

MISSING = Path("does/not/exist/text_input.yaml")


# ── config ────────────────────────────────────────────────────────────────────

def test_missing_config_uses_defaults():
    cfg = load_config(MISSING)
    assert isinstance(cfg, TextInputConfig)
    assert cfg.enabled is True
    assert cfg.menu_label and cfg.placeholder


def test_config_overrides(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text(
        "enabled: false\nmenu_label: 'Say it'\nplaceholder: 'Well?'\n",
        encoding="utf-8")
    cfg = load_config(p)
    assert cfg.enabled is False
    assert cfg.menu_label == "Say it"
    assert cfg.placeholder == "Well?"


def test_malformed_config_falls_back(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("enabled: [oops\n", encoding="utf-8")
    assert load_config(p).enabled is True


def test_empty_values_fall_back_to_defaults(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("menu_label: ''\nplaceholder: ''\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.menu_label                   # not empty
    assert cfg.placeholder                  # not empty


def test_shipped_config_is_valid():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.enabled is True
    assert cfg.menu_label and cfg.placeholder


# ── normalisation ─────────────────────────────────────────────────────────────

def test_normalize_collapses_whitespace():
    assert normalize_input("  hello   world \n again ") == "hello world again"
    assert normalize_input("a\n\nb\tc") == "a b c"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
def test_normalize_rejects_empty(bad):
    assert normalize_input(bad) is None


def test_normalize_keeps_normal_text():
    assert normalize_input("What's on my screen?") == "What's on my screen?"


def test_normalize_truncates():
    out = normalize_input("x" * (MAX_INPUT_CHARS + 500))
    assert len(out) == MAX_INPUT_CHARS
    assert normalize_input("y" * 10, max_length=0) == "y" * 10   # 0 = no cap


# ── wiring: typed turns share the voice pipeline ──────────────────────────────

class _Sink:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _FakeBus:
    def __init__(self):
        self.status = _Sink()
        self.typing = _Sink()
        self.text = _Sink()
        self.mood = _Sink()
        self.start = _Sink()
        self.end = _Sink()
        self.vision = _Sink()
        self.listening = _Sink()


class _FakeMemory:
    def __init__(self):
        self.turns = []
        self.after = 0

    def log_turn(self, role, text, mood=None):
        self.turns.append((role, text))

    def after_turn(self):
        self.after += 1


class _FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, *a, **k):
        self.said.append(a)

    def stop(self):
        pass


class _FakeConfig:
    max_history = 30
    sample_rate = 16000
    min_audio_length = 0.5
    vad_threshold_multiplier = 3.0


STREAM_RESULT = ("hi there", "joyful")


def _app():
    pytest.importorskip("PySide6")
    import app  # noqa: E402
    return app


def _responder(*, command=None):
    """A bare PetCompanion with all heavy collaborators faked out."""
    app = _app()
    obj = object.__new__(app.PetCompanion)
    obj.bus = _FakeBus()
    obj.memory = _FakeMemory()
    obj.speaker = _FakeSpeaker()
    obj.vision = None
    obj.history = []
    obj.config = _FakeConfig()
    obj.last_exchange = None
    obj.turn_lock = threading.Lock()
    obj.max_record_seconds = 10
    obj.try_command = lambda t: command
    obj._maybe_summarize = lambda: None
    obj._trim_history = lambda: None
    obj._extract_facts = lambda a, b: None
    obj.system_message = lambda query=None: {"role": "system", "content": "s"}
    obj.stream_reply = lambda messages, image_png=None: STREAM_RESULT
    return obj


def test_respond_streams_and_logs_a_full_turn():
    obj = _responder()
    obj._verb_command = lambda *_: False           # Feature 24: inert here
    obj._respond("hello there")
    assert obj.history[0] == {"role": "user", "content": "hello there"}
    assert obj.history[-1] == {"role": "assistant", "content": "hi there"}
    assert [t[0] for t in obj.memory.turns] == ["user", "assistant"]
    assert obj.memory.after == 1
    assert obj.last_exchange == ("hello there", "hi there")
    assert (True,) in obj.bus.typing.calls          # typing indicator shown


def test_respond_command_path_skips_the_llm():
    obj = _responder(command="Opening Spotify.")
    obj._respond("open spotify")
    assert obj.speaker.said[0][0] == "Opening Spotify."
    assert obj.history == []                         # no LLM turn recorded
    assert [t[0] for t in obj.memory.turns] == ["user", "assistant"]


def test_voice_turn_still_uses_respond(monkeypatch):
    """The refactor must not change the voice path."""
    obj = _responder()
    app = _app()
    # Features 21+22: the capture goes through the _record_voice wrapper;
    # patch it on the INSTANCE (raising=False: under the headless stub the
    # class hierarchy may not actually define it).
    monkeypatch.setattr(obj, "_record_voice", lambda: [0.0] * 16000,
                        raising=False)
    obj.transcribe = lambda audio: "spoken words"
    obj._missed_ack = lambda: None
    seen = {}
    obj._respond = lambda text: seen.setdefault("text", text)

    obj._run_turn()

    assert seen["text"] == "spoken words"
    assert not obj.turn_lock.locked()                # lock released


def test_start_text_turn_runs_the_same_pipeline(monkeypatch):
    obj = _responder()
    app = _app()
    started = []

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(app.threading, "Thread", _FakeThread)
    seen = {}
    obj._respond = lambda text: seen.setdefault("text", text)

    obj.start_text_turn("  hello  ")                 # surrounded by whitespace
    assert started, "a worker thread should be spawned"
    target, args = started[0]
    assert args == ("hello",)                         # stripped before dispatch
    target(*args)                                    # run it
    assert seen["text"] == "hello"
    assert not obj.turn_lock.locked()


def test_start_text_turn_rejects_empty_and_busy():
    obj = _responder()
    obj.start_text_turn("   ")
    assert obj.bus.status.calls[-1] == ("(nothing typed)", False)

    # Feature 19: a busy pet is CUT IN ON, not refused — the only refusal
    # left is when the cut-in itself fails to free the turn in time.
    obj.turn_lock.acquire()
    obj._run_text_turn = lambda text: None            # don't actually run one
    cuts = {"ok": True}
    obj._cut_in = lambda timeout_s=4.0: cuts["ok"]

    cuts["ok"] = False
    obj.start_text_turn("hi again")
    assert "couldn't cut in" in obj.bus.status.calls[-1][0]

    cuts["ok"] = True
    obj.start_text_turn("cutting in!")
    assert "cutting in!" in obj.bus.status.calls[-1][0]
    obj.turn_lock.release()


def test_text_and_voice_share_one_history():
    obj = _responder()
    obj._verb_command = lambda *_: False           # Feature 24: inert here
    obj._respond("typed hello")
    obj._respond("spoken hello")
    roles = [m["role"] for m in obj.history]
    assert roles == ["user", "assistant", "user", "assistant"]


# ── Feature 8B: inline bubble input — config ──────────────────────────────────

def test_mode_defaults_to_inline():
    assert ("inline", "window") == MODES
    assert load_config(MISSING).mode == "inline"


def test_mode_window_parsed(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("enabled: true\nmode: window\n", encoding="utf-8")
    assert load_config(p).mode == "window"


def test_unknown_mode_falls_back_to_inline(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("mode: banana\n", encoding="utf-8")
    assert load_config(p).mode == "inline"


def test_mode_is_normalised(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("mode: '  INLINE '\n", encoding="utf-8")
    assert load_config(p).mode == "inline"


def test_inline_placeholder_default_and_override(tmp_path):
    default = load_config(MISSING)
    assert default.inline_placeholder and "Enter" in default.inline_placeholder
    p = tmp_path / "text_input.yaml"
    p.write_text("inline_placeholder: 'Say something…'\n", encoding="utf-8")
    assert load_config(p).inline_placeholder == "Say something…"


def test_shipped_config_is_inline():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.enabled is True
    assert cfg.mode == "inline"
    assert cfg.inline_placeholder and cfg.menu_label and cfg.hotkey


def test_malformed_file_keeps_inline_defaults(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("enabled: [oops\n", encoding="utf-8")
    assert load_config(p).mode == "inline"


# ── Feature 8B: return_delay (input waits after her TTS really ends) ──────────

def test_return_delay_defaults_to_two_seconds():
    assert load_config(MISSING).return_delay == 2.0
    assert load_config().return_delay == 2.0        # shipped config


def test_return_delay_parsed_and_clamped(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("return_delay: 0.5\n", encoding="utf-8")
    assert load_config(p).return_delay == 0.5

    p.write_text("return_delay: 0\n", encoding="utf-8")
    assert load_config(p).return_delay == 0.0

    p.write_text("return_delay: -3\n", encoding="utf-8")
    assert load_config(p).return_delay == 0.0      # negatives clamp


def test_bad_return_delay_falls_back(tmp_path):
    p = tmp_path / "text_input.yaml"
    p.write_text("return_delay: soon\n", encoding="utf-8")
    assert load_config(p).return_delay == 2.0


# ── Feature 8B: the pure slot state machine ───────────────────────────────────

def test_inline_starts_in_input_mode():
    st = InlineBubbleState(inline=True)
    assert st.inline and st.mode == "input"


def test_show_text_switches_to_speech():
    st = InlineBubbleState()
    assert st.on_show("Hi there.") == "speech"
    assert st.mode == "speech"


def test_show_empty_hides_the_slot():
    st = InlineBubbleState()
    assert st.on_show("") == "hidden"        # a turn is running
    assert st.mode == "hidden"


def test_hide_timeout_returns_to_input_when_idle():
    st = InlineBubbleState()
    st.on_show("Her words.")
    assert st.on_hide_timeout(busy=False) == "input"
    assert st.mode == "input"


def test_hide_timeout_retries_while_busy():
    st = InlineBubbleState()
    st.on_show("Her words.")
    assert st.on_hide_timeout(busy=True) == "retry"
    assert st.mode == "speech"               # nothing changed yet
    # audio finished → the next timeout lands the input
    assert st.on_hide_timeout(busy=False) == "input"


def test_tap_while_speaking_is_ignored():
    st = InlineBubbleState()
    st.on_show("Mid-sentence!")
    assert st.on_tap(busy=True) == "noop"
    assert st.mode == "speech"               # her words were not wiped


def test_tap_when_idle_swaps_to_input():
    st = InlineBubbleState()
    st.on_show("Done speaking.")
    assert st.on_tap(busy=False) == "input"
    assert st.mode == "input"


def test_submit_normalizes_and_rejects_empty():
    assert InlineBubbleState.on_submit("  hello   there ") == "hello there"
    assert InlineBubbleState.on_submit("") is None
    assert InlineBubbleState.on_submit("   \n ") is None
    assert InlineBubbleState.on_submit(None) is None


def test_window_mode_never_offers_the_input():
    st = InlineBubbleState(inline=False)
    assert st.mode == "hidden"
    assert st.on_show("Her words.") == "speech"      # label path unchanged
    assert st.on_hide_timeout(busy=False) == "hidden"  # plain bubble.hide()
    assert st.on_tap(busy=False) == "noop"
    assert st.on_show("") == "hidden"


def test_full_lifecycle():
    """Startup → speech → busy retry → idle input → tap → turn → speech → input."""
    st = InlineBubbleState()
    assert st.mode == "input"                       # resting: the text box
    assert st.on_show("She speaks.") == "speech"    # input disappears
    assert st.on_hide_timeout(busy=True) == "retry" # still talking
    assert st.on_hide_timeout() == "input"          # back to the text box
    assert st.on_tap() == "input"                   # (already there)
    assert st.on_show("") == "hidden"               # user submitted a turn
    assert st.on_show("Her reply.") == "speech"
    assert st.on_hide_timeout() == "input"          # …and back again


# ── Feature 8B: wiring tripwires (cheap, no Qt needed) ────────────────────────

def test_wired_into_the_gui():
    root = Path(__file__).parent.parent
    app_src = (root / "src" / "app.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "returnPressed.connect(self._on_input_submit)" in app_src
    assert "installEventFilter(self)" in app_src
    assert "bus.textinput.connect(pet.prompt_text_input)" in app_src
    assert "keyboard.add_hotkey(_text_hk, bus.textinput.emit)" in app_src
    assert "def focus_text_input" in app_src
    assert "_on_hide_timeout" in app_src
    cfg_src = (root / "config" / "text_input.yaml").read_text(
        encoding="utf-8", errors="ignore")
    assert "mode: inline" in cfg_src
    assert "inline_placeholder" in cfg_src
