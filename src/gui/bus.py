"""Qt signal bus shared by the worker threads and the GUI thread.

Every cross-thread UI event goes through this object.  Keeping the signal names
in one small module makes the producer/consumer contract easy to audit.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    """Signals emitted by the companion and consumed by the pet window."""

    start = Signal()              # a turn started
    text = Signal(str)            # incremental display text
    mood = Signal(str)            # mood tag detected
    body = Signal(object)         # sentence audio start: (gen, text, tags)
    end = Signal()                # a turn finished
    status = Signal(str, bool)    # (message, keep_visible)
    proact = Signal(str)          # proactive message
    typing = Signal(bool)         # show/hide the typing indicator
    listening = Signal(bool)      # microphone recording state
    vision = Signal(str)          # screenshot armed status
    voice_issue = Signal(str)     # TTS failed; keep the text visible
    textinput = Signal()          # hotkey -> focus the inline input


__all__ = ["Bus"]
