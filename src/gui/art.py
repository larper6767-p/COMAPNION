"""Sprite artwork loading and placeholder rendering.

The Live2D renderer is optional; this module is the fallback that always works.
It deliberately owns only art concerns: no window geometry, no state, no I/O
beyond reading an image path.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor, QMovie, QPainter, QPen, QPixmap,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ART_DIR = PROJECT_ROOT / "characters" / "marin"
SIZE = 400

MOOD_COLORS = {
    "idle": "#b8a6ff",
    "joyful": "#ffd56b",
    "excited": "#ff9ecb",
    "embarrassed": "#ff8f8f",
    "sad": "#8fb8ff",
    "angry": "#ff6b6b",
    "surprised": "#9be7ff",
    "curious": "#b9f29a",
    "relaxed": "#c9b8ff",
}


def configure(*, size: int | None = None, art_dir: Path | None = None) -> None:
    """Update the fallback renderer's globals after config/CLI resolution."""
    global SIZE, ART_DIR
    if size is not None:
        SIZE = int(size)
    if art_dir is not None:
        ART_DIR = Path(art_dir)


def placeholder(mood: str, mouth_open: bool, size: int | None = None) -> QPixmap:
    """Draw a simple mood-colored face when no sprite file is available."""
    s = size if size is not None else SIZE
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    pen_w = max(2, s // 55)
    p.setPen(QPen(QColor("#2a2150"), pen_w))
    p.setBrush(QColor(MOOD_COLORS.get(mood, MOOD_COLORS["idle"])))

    margin = s // 22
    p.drawEllipse(margin, margin, s - margin * 2, s - margin * 2)
    p.setBrush(QColor("#2a2150"))

    eye_y = int(s * 0.39)
    eye_w = s // 11
    eye_h = int(s / 7.86)
    p.drawEllipse(int(s * 0.32), eye_y, eye_w, eye_h)
    p.drawEllipse(int(s * 0.59), eye_y, eye_w, eye_h)

    mouth_x = int(s * 0.41)
    mouth_w = int(s / 5.5)
    if mouth_open:
        p.drawEllipse(mouth_x, int(s * 0.61), mouth_w, int(s / 7.33))
    else:
        p.setBrush(Qt.NoBrush)
        sad = mood in ("sad", "angry")
        arc_y = int(s * (0.66 if sad else 0.59))
        arc_span = 180 * 16 if sad else -180 * 16
        p.drawArc(
            mouth_x - 10,
            arc_y,
            int(s * 0.27),
            int(s / 7.33),
            0 if sad else 180 * 16,
            arc_span,
        )
    p.end()
    return pm


def load_art(mood: str, mouth_open: bool) -> tuple[str | None, bool]:
    """Return ``(path, is_gif)`` for the best matching sprite, if any."""
    names: list[str] = []
    if mouth_open:
        names += [f"{mood}_open.gif", f"{mood}_open.png"]
    names += [f"{mood}.gif", f"{mood}.png", "idle.gif", "idle.png"]
    for name in names:
        path = ART_DIR / name
        if path.exists():
            return str(path), name.endswith(".gif")
    return None, False


def load_pixmap(mood: str, mouth_open: bool, size: int | None = None) -> QPixmap:
    """Load a mood sprite at the requested height, or draw the fallback."""
    s = size if size is not None else SIZE
    path, is_gif = load_art(mood, mouth_open)
    if path is None:
        return placeholder(mood, mouth_open, s)
    if is_gif:
        movie = QMovie(path)
        movie.jumpToFrame(0)
        pixmap = movie.currentPixmap()
        if pixmap.isNull():
            return placeholder(mood, mouth_open, s)
        return pixmap.scaledToHeight(s, Qt.SmoothTransformation)
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return placeholder(mood, mouth_open, s)
    return pixmap.scaledToHeight(s, Qt.SmoothTransformation)


__all__ = [
    "ART_DIR", "MOOD_COLORS", "SIZE", "configure", "load_art", "load_pixmap",
    "placeholder",
]
