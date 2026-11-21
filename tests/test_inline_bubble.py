"""Feature 8B UI smoke: the inline text input sharing the speech-bubble slot.

Runs offscreen (QT_QPA_PLATFORM=offscreen) on a LIGHT Pet harness that skips
the heavy ``__init__`` (no Live2D, no audio hardware, no LLM) but runs the
**real** bubble methods from ``app.Pet`` — so the slot flip, the tap, Esc and
Enter are exercised on real Qt widgets.

Guards: PySide6 optional (skipped when absent); ``_companion_ref`` is always
monkeypatched to None/a recorder, so no companion/turn ever starts.
"""
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.text_input import InlineBubbleState  # noqa: E402


def _app_module():
    pytest.importorskip("PySide6")
    import app
    return app


def _qapp():
    from PySide6.QtWidgets import QApplication
    inst = QApplication.instance()
    if inst is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        inst = QApplication([])
    return inst


@pytest.fixture()
def app_mod():
    mod = _app_module()
    _qapp()
    return mod


@pytest.fixture()
def make_pet(app_mod, monkeypatch):
    """Factory building a light Pet with only the bubble slot wired up."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

    monkeypatch.setattr(app_mod, "_companion_ref", None, raising=False)

    class _SmokePet(app_mod.Pet):
        def __init__(self, inline=True):
            QWidget.__init__(self)          # deliberately skips Pet.__init__
            self.bubble = QLabel(self)
            self.bubble.setWordWrap(True)
            self.bubble.hide()
            self.bubble_text = ""
            self.typing_label = QLabel(self)
            self.typing_label.hide()
            self._bubble_state = InlineBubbleState(inline)
            # The smoke pet stands in for "the user is interacting with Marin":
            # hover-reveal exposes the slot, so the state-machine tests below
            # assert on the visible slot contents.
            self._hovering = True
            # Manga side-bubble fields (real Pet gets these in __init__; the
            # light harness turns the feature OFF so the smoke tests keep
            # exercising the original above/below placement path).
            self._use_side_bubble = False
            self._bubble_shown = False
            self._face_x_px = 0
            self._bubble_tail = QLabel(self)
            self._bubble_tail.hide()
            self.input_bubble = None
            if inline:
                self.input_bubble = QLineEdit(self)
                self.input_bubble.setPlaceholderText("Type here… Enter to send")
                self.input_bubble.returnPressed.connect(self._on_input_submit)
                self.input_bubble.installEventFilter(self)
                self.bubble.installEventFilter(self)
                self.input_bubble.setFixedHeight(44)
                self.input_bubble.setVisible(True)
            self.hide_timer = QTimer(
                self, singleShot=True, timeout=self._on_hide_timeout)
            # Feature 8B grace period: input waits 2s after TTS really ends.
            self._return_delay_ms = 2000
            self._input_grace_armed = False
            self._input_grace_timer = QTimer(
                self, singleShot=True, interval=self._return_delay_ms,
                timeout=self._on_input_grace)
            # geometry used by _place_bubble
            self._bubble_h = 80
            self._face_top = 84
            self._face_size = 200
            self._win_width = 240
            self.setFixedSize(240, 364)
            if self.input_bubble is not None:
                self._place_bubble()
            self.show()

    def _build(inline=True):
        return _SmokePet(inline=inline)

    return _build


@pytest.fixture()
def pet(make_pet):
    return make_pet(inline=True)


def _press(widget):
    """Deliver a real left-press event to ``widget`` (goes through eventFilter)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(4, 4), QPointF(4, 4),
                     QPointF(4, 4), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def _escape(widget):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape,
                                             Qt.NoModifier))


# ── slot behaviour (real widgets, real methods) ───────────────────────────────

def test_input_starts_in_the_slot(pet):
    assert pet.input_bubble is not None
    assert pet.input_bubble.isVisible()
    assert not pet.bubble.isVisible()
    assert pet.input_bubble.y() > 0                       # actually placed
    assert pet.typing_label.y() == pet.input_bubble.y()   # dots sit in the slot


def test_speech_takes_the_slot(pet):
    pet.show_bubble("Hi there.")
    assert pet.bubble.isVisible()
    assert pet.bubble.text() == "Hi there."
    assert not pet.input_bubble.isVisible()


def test_turn_start_clears_the_slot(pet):
    pet.show_bubble("Her words.")
    pet.show_bubble("")            # on_start: a turn owns the slot
    assert not pet.bubble.isVisible()
    assert not pet.input_bubble.isVisible()


def test_hide_timeout_returns_to_input_after_the_grace_period(pet):
    """Input waits for TTS-to-really-be-done + return_delay (default 2s)."""
    pet.show_bubble("Done speaking.")
    pet._on_hide_timeout()
    assert not pet.input_bubble.isVisible()          # grace countdown started
    assert pet._input_grace_armed
    assert pet._input_grace_timer.isActive()
    pet._on_input_grace()                            # 2s elapsed
    assert pet.input_bubble.isVisible()
    assert not pet.bubble.isVisible()


def test_hide_timeout_retries_while_generating(pet):
    pet.show_bubble("Done speaking.")
    pet.typing_label.show()        # generation owns the slot
    pet._on_hide_timeout()
    assert not pet.input_bubble.isVisible()       # input stayed away
    assert not pet._input_grace_armed             # grace not even armed yet
    assert pet.hide_timer.isActive()              # poll armed
    pet.typing_label.hide()
    pet._on_hide_timeout()                        # idle now → grace starts
    assert pet._input_grace_armed
    assert not pet.input_bubble.isVisible()
    pet._on_input_grace()
    assert pet.input_bubble.isVisible()           # …and back it comes


def test_grace_restarts_when_she_speaks_again(pet):
    pet.show_bubble("First reply.")
    pet._on_hide_timeout()
    assert pet._input_grace_armed                 # counting down…
    pet.show_bubble("Second reply.")              # …but she talks again
    assert not pet._input_grace_armed
    pet._on_hide_timeout()
    assert pet._input_grace_armed                 # fresh countdown, not instant
    assert not pet.input_bubble.isVisible()
    pet._on_input_grace()
    assert pet.input_bubble.isVisible()


def test_input_stays_away_if_she_resumes_during_grace(pet):
    pet.show_bubble("Done speaking.")
    pet._on_hide_timeout()                        # grace armed
    pet.typing_label.show()                       # new turn kicks in
    pet._on_input_grace()
    assert not pet.input_bubble.isVisible()       # busy → no swap


def test_tap_on_her_bubble_swaps_to_input(pet):
    pet.show_bubble("Done speaking.")
    _press(pet.bubble)
    assert pet.input_bubble.isVisible()
    assert not pet.bubble.isVisible()


def test_esc_clears_the_line(pet):
    pet.input_bubble.setText("draft")
    _escape(pet.input_bubble)
    assert pet.input_bubble.text() == ""


def test_enter_sends_the_cleaned_message(pet, app_mod, monkeypatch):
    sent = []

    class _Companion:
        turn_lock = threading.Lock()
        def start_text_turn(self, text):
            sent.append(text)

    monkeypatch.setattr(app_mod, "_companion_ref", _Companion(), raising=False)
    pet.input_bubble.setText("  hello   there ")
    pet._on_input_submit()
    assert sent == ["hello there"]
    assert pet.input_bubble.text() == ""
    assert not pet.input_bubble.isVisible()        # slot released to the turn


def test_empty_enter_stays_in_the_input(pet):
    pet.input_bubble.setText("   \n ")
    pet._on_input_submit()                         # nothing to send → no-op
    assert pet.input_bubble.isVisible()
    assert pet.input_bubble.text() == "   \n "     # untouched


def test_window_mode_never_builds_the_input(make_pet):
    p = make_pet(inline=False)
    assert p.input_bubble is None
    p.show_bubble("Hi.")
    assert p.bubble.isVisible()
    p._on_hide_timeout()
    assert not p.bubble.isVisible()                # plain hide (Feature 5 as shipped)


# ── hover-reveal (bubble/input only while hovering or speaking) ──────────────

def test_not_hovering_hides_the_resting_input(pet):
    """Idle + pointer away → the type box stays out of the way."""
    pet._hovering = False
    pet._sync_slot_visibility()
    assert not pet.input_bubble.isVisible()
    assert not pet.bubble.isVisible()


def test_hovering_reveals_the_input(pet):
    """Pointer over the pet → the type box appears for typing."""
    pet._hovering = False
    pet._sync_slot_visibility()
    assert not pet.input_bubble.isVisible()
    pet._hovering = True
    pet._sync_slot_visibility()
    assert pet.input_bubble.isVisible()
    assert not pet.bubble.isVisible()


def test_leaving_hides_her_words_once_silent(pet):
    """She stops talking and the pointer leaves → speech bubble hides."""
    pet.show_bubble("Her last words.")            # speaking (hovered)
    assert pet.bubble.isVisible()
    pet._hovering = False
    pet._apply_slot(pet._bubble_state.on_hide_timeout(busy=False))
    assert not pet.bubble.isVisible()
    assert not pet.input_bubble.isVisible()


def test_hovering_while_idle_restores_draft_text(pet):
    """The draft survives hiding and comes back on hover."""
    pet.input_bubble.setText("half a message")
    pet._hovering = False
    pet._sync_slot_visibility()
    assert not pet.input_bubble.isVisible()
    pet._hovering = True
    pet._sync_slot_visibility()
    assert pet.input_bubble.isVisible()
    assert pet.input_bubble.text() == "half a message"
