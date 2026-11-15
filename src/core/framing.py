"""Feature 15: the model FRAME — a model-space crop that fills the face square.

Why a frame
-----------
Until now the model's on-screen size was derived from the window: the app
guessed ``px_per_unit = face_size / (2 * model_scale)`` while the preview
tool used ``width / 2 * scale`` - two opposite conventions for the same
thing.  Both were guesses, so panning was slippery, the touch zones needed an
``anchor`` + ``zoom_invert`` calibration hack, and resizing the window
silently re-framed the model.

A *frame* removes the guesswork: it is a rectangle in MODEL space (the same
space the touch zones and the physics rig live in) that is exactly what the
face square shows.  Everything else follows from it:

* window size  -> only changes how many pixels the frame occupies,
* model zoom   -> changes the frame's size,
* model pan    -> changes the frame's position,
* touch zones  -> map through the frame, exactly, with no calibration.

The frame is a pure value object; the Live2D SDK specifics (canvas size,
the scale/offset it needs) are handled by the widget, which measures the
runtime and converts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

# The SDK's offset convention: +offset_x moves the MODEL to the right, so
# the region we see moves to the LEFT in model space; +offset_y moves the
# model UP (screen up), so the visible region moves DOWN in model space
# (model y points up).  In other words:
#     visible_centre = canvas_centre - offset
# which is the relation both conversions below use.


@dataclass(frozen=True)
class Frame:
    """A rectangle in model space (Live2D units, y up) shown by the face
    square.  ``w``/``h`` are the visible extents; the frame's centre is what
    the square's centre shows."""

    x: float = 0.0
    y: float = 0.0
    w: float = 10.0
    h: float = 10.0

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2.0

    def contains(self, mx: float, my: float) -> bool:
        return (self.x <= mx <= self.x + self.w
                and self.y <= my <= self.y + self.h)

    def moved(self, dx: float, dy: float) -> "Frame":
        return Frame(self.x + dx, self.y + dy, self.w, self.h)

    def zoomed(self, factor: float, keep_center: bool = True) -> "Frame":
        """Zoom the frame about its centre (factor > 1 = closer)."""
        if factor <= 0.0:
            return self
        if keep_center:
            w = self.w / factor
            h = self.h / factor
            return Frame(self.center_x - w / 2.0, self.center_y - h / 2.0, w, h)
        return Frame(self.x, self.y, self.w / factor, self.h / factor)

    def fitted_square(self, side: float) -> "Frame":
        """Same centre, square of the given model-space side."""
        return Frame(self.center_x - side / 2.0, self.center_y - side / 2.0,
                     side, side)

    def resized(self, new_w: float, new_h: float,
                keep_center: bool = True) -> "Frame":
        """New size, same centre (or the same top-left corner).

        Used by the frame editor: changing the HEIGHT crops the model
        vertically (a taller window shows less of her body)."""
        if new_w <= 0.0 or new_h <= 0.0:
            return self
        if keep_center:
            return Frame(self.center_x - new_w / 2.0, self.center_y - new_h / 2.0,
                         new_w, new_h)
        return Frame(self.x, self.y, new_w, new_h)

    @property
    def aspect(self) -> float:
        """width / height (the window's aspect follows this)."""
        if self.h <= 0.0:
            return 1.0
        return self.w / self.h

    def as_dict(self) -> dict:
        return {"x": round(self.x, 6), "y": round(self.y, 6),
                "w": round(self.w, 6), "h": round(self.h, 6)}

    @staticmethod
    def from_dict(raw) -> Optional["Frame"]:
        if not isinstance(raw, dict):
            return None
        try:
            x = float(raw["x"]); y = float(raw["y"])
            w = float(raw["w"]); h = float(raw["h"])
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            return None
        if w <= 0.0 or h <= 0.0:
            return None
        return Frame(x, y, w, h)


@dataclass(frozen=True)
class Framing:
    """A frame + the canvas (face rectangle) it is drawn into.

    The canvas is a RECTANGLE, not a square: its width is the size the user
    picks (the pet's "face size"), and its height follows the frame's aspect
    (so a taller frame = a taller window = her body cropped vertically).
    """

    frame: Frame
    width_px: float
    height_px: float

    @property
    def square_px(self) -> float:
        """Backwards-compatible alias (the width)."""
        return self.width_px

    @property
    def px_per_unit(self) -> float:
        if self.frame.w <= 0.0:
            return 1.0
        return self.width_px / self.frame.w

    def model_to_px(self, mx: float, my: float) -> Tuple[float, float]:
        """Model units -> canvas-local pixels (y down, origin = centre)."""
        ppu = self.px_per_unit
        px = self.width_px * 0.5 + (mx - self.frame.center_x) * ppu
        py = self.height_px * 0.5 - (my - self.frame.center_y) * ppu
        return px, py

    def px_to_model(self, px: float, py: float) -> Tuple[float, float]:
        """Canvas-local pixels -> model units."""
        ppu = self.px_per_unit
        mx = self.frame.center_x + (px - self.width_px * 0.5) / ppu
        my = self.frame.center_y - (py - self.height_px * 0.5) / ppu
        return mx, my

    def rect_px(self, rect: Tuple[float, float, float, float]) -> \
            Tuple[float, float, float, float]:
        """Model-space rect (x, y, w, h) -> (left, top, right, bottom) px."""
        x, y, w, h = rect
        l, bot = self.model_to_px(x, y)
        r, top = self.model_to_px(x + w, y + h)
        return (l, top, r, bot)


def fit_frame(frame: Frame, width_px: float, height_px: float) -> Frame:
    """Fit a frame to a canvas aspect, cropping the excess (the "cover" rule).

    The frame's WIDTH always fills the canvas width; if the canvas is
    taller than the frame's aspect, the frame's height is cropped (the model
    is cropped vertically, never stretched)."""
    if frame.w <= 0.0 or width_px <= 0.0:
        return frame
    target_h = frame.w * (height_px / width_px)
    if abs(target_h - frame.h) < 1e-9:
        return frame
    return Frame(frame.center_x - frame.w / 2.0,
                 frame.center_y - target_h / 2.0,
                 frame.w, target_h)


# ── frame <-> the SDK's (scale, offset) ──────────────────────────────────────
#
# The widget measures the runtime: canvas size in model units and the canvas
# size in pixels give the exact px-per-unit at the current scale.  From that
# we can convert a frame into the (scale, offset) the SDK needs - and back -
# without assuming how the SDK fits the canvas into the widget.

def frame_to_scale_offset(frame: Frame, canvas_w: float, canvas_h: float,
                          pu_per_scale: float,
                          width_px: float, height_px: float) -> \
        Tuple[float, float, float]:
    """Frame -> (scale, offset_x, offset_y) for the SDK.

    ``pu_per_scale`` = pixels per model unit at scale 1.0 (measured by the
    widget: ``canvas_px_per_unit / current_scale``).  We want the frame's
    width to fill the canvas width, so the scale follows from
    ``width_px / frame.w``.  The visible region's centre sits at the canvas
    centre shifted by the offset (the SDK's +offset moves the model, so the
    visible region moves the other way).

    The result is window-size independent: the SDK's own fit scales the
    model with the widget, so the same frame is shown at any window size -
    only its pixel size changes.
    """
    if frame.w <= 0.0 or pu_per_scale <= 0.0 or canvas_w <= 0.0 \
            or width_px <= 0.0:
        return 1.0, 0.0, 0.0
    frame = fit_frame(frame, width_px, height_px)
    want_pu = width_px / frame.w            # px per model unit we want
    scale = want_pu / pu_per_scale
    off_x = canvas_w / 2.0 - frame.center_x
    off_y = canvas_h / 2.0 - frame.center_y
    return scale, off_x, off_y


def scale_offset_to_frame(scale: float, offset_x: float, offset_y: float,
                          canvas_w: float, canvas_h: float,
                          pu_per_scale: float, width_px: float,
                          height_px: float) -> Frame:
    """(scale, offset_x, offset_y) -> the frame the canvas shows."""
    if canvas_w <= 0.0 or canvas_h <= 0.0 or pu_per_scale <= 0.0:
        return Frame(0.0, 0.0, 1.0, 1.0)
    pu = pu_per_scale * max(1e-6, scale)
    vis_w = width_px / pu                   # model units across the width
    vis_h = height_px / pu                 # ... and down the height
    cx = canvas_w / 2.0 - offset_x
    cy = canvas_h / 2.0 - offset_y
    return Frame(cx - vis_w / 2.0, cy - vis_h / 2.0, vis_w, vis_h)


def resize_edge_at(fx: float, fy: float, size: float,
                   band: float = 10.0) -> Optional[str]:
    """Which resize edge a face-local point sits on ("left"/"right"/None).

    The pet's window is wider than the model square, so the empty margin on
    each side of the model acts as an invisible resize handle - like a
    normal window border, but without any visible chrome.  The bubble strips
    above and below the square are NOT resize areas.
    """
    if size <= 0.0:
        return None
    if not (0.0 <= fy <= size):
        return None
    if -band <= fx <= 0.0:
        return "left"
    if size <= fx <= size + band:
        return "right"
    return None


__all__ = [
    "Frame", "Framing", "fit_frame",
    "frame_to_scale_offset", "scale_offset_to_frame",
    "resize_edge_at",
]
 