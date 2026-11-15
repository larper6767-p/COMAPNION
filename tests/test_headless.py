"""Headless Qt tests — run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_headless.py -v

Stubs out she, core.*, keyboard so no hardware is needed.
"""
import sys
import types
import os

# ── Stub out heavy deps before any app import ──────────────────────────────────

def _make_stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m

# keyboard
sys.modules["keyboard"] = _make_stub("keyboard",
    add_hotkey=lambda *a, **k: None,
    unhook_all=lambda: None)

# pygame
import unittest.mock as mock
pygame_stub = _make_stub("pygame",
    error=Exception,
    mixer=mock.MagicMock())
pygame_stub.mixer.music.get_busy = mock.MagicMock(return_value=False)
pygame_stub.mixer.music.stop = mock.MagicMock()
pygame_stub.mixer.init = mock.MagicMock()
pygame_stub.mixer.music.set_volume = mock.MagicMock()
sys.modules["pygame"] = pygame_stub

# core.*
class _FakeCfg:
    persona = {"name": "Marin", "system_prompt": "x", "greeting": None,
               "temperature": 0.9, "max_reply_length": 300, "max_tokens": 200,
               "mood_tags": False, "moods": [], "reminder": "",
               "proactive_enabled": False, "proactive_idle_minutes": 10,
               "proactive_messages": [], "closeness_enabled": False,
               "closeness_tiers": {}, "summarize_after": 20}
    sample_rate = 16000; whisper_model = "tiny"; whisper_device = "cpu"
    whisper_compute_type = "int8"; whisper_beam_size = 1; whisper_language = "en"
    whisper_vad_filter = True; whisper_condition_on_previous = False
    preset_voice_id = ""; tts_model = ""; emotion_template = None
    volume = 0.7; voice_speed = 1.0; tts_cache_enabled = False
    tts_cache_max_size = 10; audio_ducking_enabled = False; audio_ducking_volume = 0.3
    vad_threshold_multiplier = 3.0; min_audio_length = 0.5; max_history = 30
    cerebras_model = ""; local_model = ""; use_cerebras = False
    wake_word_enabled = False; wake_word = ""; config_dir = None
    closeness_tiers = {}
    def load_memory(self): return []
    def save_memory(self, x): pass
    def reload(self): pass

class _FakeSpeaker:
    enabled = True
    volume = 0.7; speed = 1.0; cache = None
    def say(self, *a, **k): pass
    def stop(self): pass
    def is_playing(self): return False
    def set_volume(self, v): self.volume = v
    def set_speed(self, s): self.speed = s
    def set_emotion_template(self, t): pass
    def set_ducking(self, *a): pass
    def shutdown(self, **k): pass

class _FakeModel:
    temperature = 0.9; max_tokens = 200; repeat_penalty = 1.1; stop = []
    model_name = "fake"
    def get_token_stats(self): return {"prompt": 0, "completion": 0}

class _FakeMemory:
    def build_context_block(self): return ""
    def closeness_score(self): return 0
    def tier_name(self): return "Strangers"
    def get_facts(self): return []
    def log_turn(self, *a): pass
    def after_turn(self): pass
    def load_facts(self, f): pass
    def add_fact(self, f): pass
    def clear_facts(self): pass

class _FakeCompanion:
    config = _FakeCfg()
    speaker = _FakeSpeaker()
    model = _FakeModel()
    memory = _FakeMemory()
    history = []
    wake_detector = None
    turn_lock = __import__("threading").Lock()
    def start_turn(self): pass
    def apply_config(self): pass
    def shutdown(self): pass

# Patch she module
she_stub = _make_stub("she", VoiceCompanion=object)
sys.modules["she"] = she_stub

for mod in ["core.config","core.model","core.audio","core.memory","core.recording",
            "fishaudio","faster_whisper","sounddevice","ollama","cerebras",
            "cerebras.cloud","cerebras.cloud.sdk","dotenv","pycaw","pycaw.pycaw"]:
    sys.modules[mod] = _make_stub(mod,
        Config=_FakeCfg, Speaker=_FakeSpeaker,
        ModelInterface=_FakeModel, MemoryManager=_FakeMemory,
        load_dotenv=lambda *a,**k: None,
        parse_mood_tag=lambda t: (None, t),
        split_sentences=lambda t, min_length=20: [t],
        next_fallback=lambda: "oops",
        record_until_silence=lambda **k: None,
        InterruptMonitor=mock.MagicMock,
        WakeWordDetector=mock.MagicMock,
        WhisperModel=mock.MagicMock,
        FishAudio=mock.MagicMock,
    )

# ── Now import the actual app module ──────────────────────────────────────────
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv[:1])

import app as APP

# ── Tests ─────────────────────────────────────────────────────────────────────

SIZE   = APP.SIZE
WIDTH  = APP.WIDTH
BUBBLE_H = APP.BUBBLE_H
FACE_TOP = APP.FACE_TOP

APP._companion_ref = _FakeCompanion()


def _make_face():
    parent = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget()
    face = APP.FaceLabel(parent)
    face.setGeometry((WIDTH - SIZE) // 2, FACE_TOP, SIZE, SIZE)
    return face, parent


def test_facelabel_dedup_same_key():
    """Same (mood, mouth_open) twice must not reload art."""
    face, _ = _make_face()
    face.update_art("idle", False, crossfade=False)
    key_after_first = face._key
    face.update_art("idle", False, crossfade=False)
    assert face._key == key_after_first, "Key changed on identical call"


def test_facelabel_dedup_different_mouth():
    """Changing mouth_open must trigger a new key."""
    face, _ = _make_face()
    face.update_art("idle", False, crossfade=False)
    key1 = face._key
    face.update_art("idle", True, crossfade=False)
    key2 = face._key
    assert key1 != key2, "Key did not change when mouth_open changed"


def test_facelabel_crossfade_no_crash():
    """Crossfade start, finish, then a second update during fade must not crash."""
    face, _ = _make_face()
    face.update_art("idle", False, crossfade=True)
    # Simulate another update while fade is in flight — should abort cleanly
    face.update_art("joyful", False, crossfade=True)
    # Force swap
    face._swap_immediately()
    assert face._overlay.isHidden()


def test_bubble_below_fits_in_window():
    """With pet at screen y=0, bubble-below position must stay inside window height."""
    pet = APP.Pet(on_click=lambda: None)
    # Simulate being at the top of the screen so _place_bubble goes below
    pet.move(0, 0)
    pet.on_start()
    pet.on_text("x" * 300)
    bub_bottom = pet.bubble.y() + pet.bubble.height()
    assert bub_bottom <= pet.height(), (
        f"Bubble bottom {bub_bottom} > window height {pet.height()}"
    )


def test_typing_label_hidden_after_on_text():
    """Typing indicator must be hidden once the first text token arrives."""
    pet = APP.Pet(on_click=lambda: None)
    pet.show()   # required for isVisible() to reflect widget state in offscreen mode
    pet.on_typing(True)
    assert pet.typing_label.isVisible(), "Indicator should be visible after on_typing(True)"
    pet.on_text("hello")
    assert not pet.typing_label.isVisible(), "Indicator should hide on first on_text()"


def test_voice_issue_keeps_text_and_adds_one_note():
    """A failed TTS sentence must not turn the bubble into a silent blank."""
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    pet.on_start()
    pet.on_text("I can't say this out loud.")
    pet.on_voice_issue("I can't say this out loud.")
    assert "I can't say this out loud." in pet.bubble_text
    assert "Voice unavailable" in pet.bubble_text
    pet.on_voice_issue("I can't say this out loud.")
    assert pet.bubble_text.lower().count("voice unavailable") == 1


def test_voice_issue_does_not_move_or_resize_the_model_window():
    pet = APP.Pet(on_click=lambda: None)
    pet.move(80, 80)
    pet.show()
    pet.on_start()
    pos_before = pet.pos()
    size_before = pet.size()
    pet.on_voice_issue("A line that never got voiced.")
    assert pet.pos() == pos_before
    assert pet.size() == size_before


def test_audio_busy_no_raise_uninit():
    """_audio_busy() must return False without raising when mixer may be uninit."""
    result = APP.Pet._audio_busy()
    assert result is False


def test_gui_building_blocks_are_reexported():
    """app.py keeps its public imports stable while gui/ owns the widgets."""
    from gui import bus as gui_bus
    from gui import runtime as gui_runtime
    from gui import widgets as gui_widgets

    assert APP.Bus is gui_bus.Bus
    assert APP.FaceLabel is gui_widgets.FaceLabel
    assert APP.TypingIndicator is gui_widgets.TypingIndicator
    assert APP.StatusIndicator is gui_widgets.StatusIndicator
    assert APP.TouchZoneOverlay is gui_widgets.TouchZoneOverlay
    assert APP.BubbleTail is gui_widgets.BubbleTail
    assert APP.ProactiveTimer is gui_runtime.ProactiveTimer
    assert APP.WakeWordThread is gui_runtime.WakeWordThread

