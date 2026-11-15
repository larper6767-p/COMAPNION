"""Background timers and threads owned by the desktop pet window."""
from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Any

from PySide6.QtCore import QTimer

logger = logging.getLogger(__name__)


class ProactiveTimer:
    """Fires a proactive message after a configured period of user silence."""

    def __init__(self, companion: Any, interval_minutes: int):
        self.companion = companion
        self._interval_ms = interval_minutes * 60 * 1000
        self._timer = QTimer(singleShot=True)
        self._timer.timeout.connect(self._fire)

    def reset(self) -> None:
        """Restart the idle countdown, if proactive messages are enabled."""
        self._timer.stop()
        if self.companion.config.proactive_enabled:
            self._timer.start(self._interval_ms)

    def stop(self) -> None:
        self._timer.stop()

    def _is_ignored_long(self) -> bool:
        if self.companion._state_getter is None:
            return False
        try:
            snapshot = self.companion._state_getter()
        except Exception:
            return False
        if snapshot is None:
            return False
        seconds = getattr(snapshot, "seconds_since_interaction", None)
        return seconds is not None and seconds >= 300.0

    def _fire(self) -> None:
        companion = self.companion
        if not companion.config.proactive_pools and not companion.config.proactive_generate:
            self.reset()
            return
        if companion.turn_lock.locked():
            self.reset()
            return

        now = datetime.datetime.now()
        if not companion._proactive_scheduler.allowed(
                now, ignored_long=self._is_ignored_long()):
            self.reset()
            return

        message = companion.generate_proactive()
        if not message:
            self.reset()
            return

        threading.Thread(
            target=companion.say_proactive,
            args=(message,),
            daemon=True,
        ).start()
        companion._proactive_scheduler.record(now)
        self.reset()


class WakeWordThread:
    """Wait for the wake word without blocking the GUI or re-triggering TTS."""

    def __init__(self, companion: Any):
        self.companion = companion
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="WakeWord")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        detector = self.companion.wake_detector
        if detector is None:
            return
        logger.info("Wake-word thread started")
        while not self._stop.is_set():
            if (self.companion.turn_lock.locked()
                    or self.companion.speaker.is_playing()):
                time.sleep(0.2)
                continue
            detector.wait_for_wake()
            if not self._stop.is_set():
                logger.info("Wake word triggered - starting voice turn")
                self.companion.start_turn()
                time.sleep(1.0)


__all__ = ["ProactiveTimer", "WakeWordThread"]
