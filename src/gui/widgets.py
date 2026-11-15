"""Small, reusable widgets used by the desktop pet window.

These widgets are intentionally independent of the companion/controller.  They
only render art, status, or geometry; the ``Pet`` widget owns the layout.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
)
from PySide6.QtGui import (
    QColor, QMovie, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QLabel, QWidget,
)

from gui import art
from core.touch import (
    all_zone_rects_px as touch_all_zone_rects_px,
    get_config as get_touch_config,
)


class FaceLabel(QLabel):
    """Sprite face with a cross-faded overlay and GIF support.

    ``update_art`` is a no-op when the requested (path, mood, mouth, size) is
    unchanged, so a playing GIF is not restarted and lip-sync does not thrash
    the filesystem.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)

        self._overlay = QLabel(parent)
        self._overlay.setAlignment(Qt.AlignCenter)
        self._overlay.setGeometry(self.geometry())
        self._overlay.hide()

        self._fx = QGraphicsOpacityEffect(self._overlay)
        self._fx.setOpacity(1.0)
        self._overlay.setGraphicsEffect(self._fx)

        self._anim: QPropertyAnimation | None = None
        self._current_movie: QMovie | None = None
        self._overlay_movie: QMovie | None = None
        self._key: tuple | None = None

    def setGeometry(self, *args):  # noqa: N802 - Qt API
        super().setGeometry(*args)
        self._overlay.setGeometry(*args)

    def update_art(self, mood: str, mouth_open: bool, crossfade: bool = True) -> None:
        path, is_gif = art.load_art(mood, mouth_open)
        size = art.SIZE
        try:
            size = self.parent().face_size  # type: ignore[union-attr]
        except Exception:
            pass

        key = (path, mood, mouth_open, size) if path else (
            "placeholder", mood, mouth_open, size)
        if key == self._key:
            return
        self._key = key
        self._abort_fade()

        if path is None:
            self._overlay.setPixmap(art.placeholder(mood, mouth_open, size))
            if self._overlay_movie:
                self._overlay_movie.stop()
                self._overlay_movie = None
        elif is_gif:
            if self._overlay_movie:
                self._overlay_movie.stop()
            self._overlay_movie = self._scaled_movie(path, size)
            self._overlay.setMovie(self._overlay_movie)
            self._overlay_movie.start()
        else:
            if self._overlay_movie:
                self._overlay_movie.stop()
                self._overlay_movie = None
            self._overlay.setPixmap(
                QPixmap(path).scaledToHeight(size, Qt.SmoothTransformation))

        if not crossfade:
            self._swap_immediately()
            return

        self._fx.setOpacity(0.0)
        self._overlay.raise_()
        self._overlay.show()

        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(250)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._swap_immediately)
        self._anim.start()

    def _abort_fade(self) -> None:
        if self._anim is not None:
            try:
                self._anim.finished.disconnect(self._swap_immediately)
            except (RuntimeError, TypeError):
                pass
            self._anim.stop()
            self._anim = None
        self._overlay.hide()

    @staticmethod
    def _scaled_movie(path: str, size: int | None = None) -> QMovie:
        s = size if size is not None else art.SIZE
        movie = QMovie(path)
        movie.jumpToFrame(0)
        image_size = movie.currentImage().size()
        if image_size.height() > 0:
            width = image_size.width() * s // image_size.height()
            movie.setScaledSize(QSize(width, s))
        return movie

    def _swap_immediately(self) -> None:
        if self._overlay_movie:
            if self._current_movie and self._current_movie is not self._overlay_movie:
                self._current_movie.stop()
            self._current_movie = self._overlay_movie
            self._overlay_movie = None
            super().setMovie(self._current_movie)
        else:
            if self._current_movie:
                self._current_movie.stop()
                self._current_movie = None
            pixmap = self._overlay.pixmap()
            super().setPixmap(pixmap if pixmap is not None else QPixmap())
        self._overlay.hide()
        self._anim = None


class TypingIndicator(QLabel):
    """Pulsing ``...`` dots while the model is generating."""

    _FRAMES = ["   ", ".  ", ".. ", "..."]

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "background: rgba(245,245,240,235); color: #444444; border-radius: 10px;"
            "font-size: 22px; font-weight: bold; padding: 6px 14px;"
        )
        self._frame = 0
        self._timer = QTimer(self, interval=350, timeout=self._tick)
        self.hide()

    def show_indicator(self) -> None:
        self._frame = 0
        self.setText(self._FRAMES[0])
        self.adjustSize()
        self.show()
        self._timer.start()

    def hide_indicator(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(self._FRAMES)
        self.setText(self._FRAMES[self._frame])


class StatusIndicator(QLabel):
    """Small mic/speaker pill shown in the corner of the pet."""

    _STYLES = {
        "listening": (
            "background: rgba(220,50,50,210); color: white; border-radius: 10px;"
            "font-size: 14px; padding: 3px 8px;"
        ),
        "talking": (
            "background: rgba(100,60,200,210); color: white; border-radius: 10px;"
            "font-size: 14px; padding: 3px 8px;"
        ),
    }
    _LABELS = {"listening": "🎤", "talking": "🔊"}

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self._state = "idle"
        self._alpha = 255
        self._rising = False
        self._pulse_timer = QTimer(self, interval=40, timeout=self._pulse_tick)
        self.hide()

    def set_state(self, state: str) -> None:
        """Set ``listening``, ``talking``, or ``idle``."""
        self._state = state
        if state == "idle":
            self._pulse_timer.stop()
            self.hide()
            return
        self.setText(self._LABELS[state])
        self.setStyleSheet(self._STYLES[state])
        self.adjustSize()
        self._alpha = 255
        self._rising = False
        self._pulse_timer.start()
        self.show()
        self.raise_()

    def _pulse_tick(self) -> None:
        step = 12
        if self._rising:
            self._alpha = min(255, self._alpha + step)
            if self._alpha >= 255:
                self._rising = False
        else:
            self._alpha = max(140, self._alpha - step)
            if self._alpha <= 140:
                self._rising = True
        self.setWindowOpacity(self._alpha / 255)


class TouchZoneOverlay(QWidget):
    """Translucent debug overlay for the configured touch zones.

    The overlay is click-through and recomputes zones at paint time, so it
    follows resize, zoom, and pan without any bookkeeping.
    """

    _COLORS = {
        "head": QColor(255, 168, 60),
        "face": QColor(110, 200, 255),
        "shoulders": QColor(160, 255, 140),
    }

    def __init__(self, pet, parent: QWidget):
        super().__init__(parent)
        self._pet = pet
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):  # noqa: N802 - Qt API
        cfg = get_touch_config()
        if not cfg.enabled:
            return
        framing = self._pet._touch_framing(cfg)
        if framing is None:
            return

        painter = QPainter(self)
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        for name, (left, top, right, bottom) in touch_all_zone_rects_px(
                cfg, framing).items():
            color = self._COLORS.get(name, QColor(240, 240, 240))
            fill = QColor(color)
            fill.setAlpha(34)
            painter.setPen(QPen(color, 2))
            painter.setBrush(fill)
            painter.drawRect(QRectF(left, top, right - left, bottom - top))
            painter.setPen(Qt.white)
            painter.drawText(
                int(left) + 6,
                int(top) + 16,
                f"{name}  ({left:.0f},{top:.0f})-({right:.0f},{bottom:.0f})",
            )


class BubbleTail(QWidget):
    """Small manga-style triangle pointing from the bubble toward her head."""

    def __init__(self, parent: QWidget, size: int = 14, color: str = "#f2f2f0"):
        super().__init__(parent)
        self._size = max(6, int(size))
        self._color = QColor(color)
        self._flip = False
        self.setFixedSize(self._size, self._size)
        self.hide()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        color = QColor(self._color)
        color.setAlpha(200)
        painter.setBrush(color)
        size = self._size
        if self._flip:
            points = [QPoint(size, size // 2), QPoint(0, 0), QPoint(0, size)]
        else:
            points = [QPoint(0, size // 2), QPoint(size, 0), QPoint(size, size)]
        painter.drawPolygon(points)
        painter.end()

    def set_flip(self, flip: bool) -> None:
        self._flip = bool(flip)
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()


__all__ = [
    "BubbleTail", "FaceLabel", "StatusIndicator", "TouchZoneOverlay",
    "TypingIndicator",
]
