#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Live2D model preview and tuning tool.

Standalone window — run from the project root:

    .venv\\Scripts\\python tools\\live2d_preview.py

Three ways to look at the model
──────────────────────────────
  "free look"  (default) - the sliders and the mouse drive the model
                            directly (scale / offset).
  "zones"      [Z]        - drag the touch zones on the model.
  "frame"      [F]        - drag the model FRAME (the area the pet shows).
                            The bottom-right corner changes the frame's
                            HEIGHT -> the pet's window becomes taller and
                            her body is cropped vertically.

Mouse controls on the Live2D canvas
────────────────────────────────────
  Left-drag     free look: pan the model  |  zones: move a zone
                                          |  frame: move the frame
  Scroll up     free look: zoom in  |  zones: next zone  |  frame: zoom
  Scroll down   the opposite
  Bottom-right  (frame mode only) drag the corner -> the frame's height

Keyboard controls
─────────────────
  1-9 / 0       jump to a mood
  ← →           previous / next mood
  ↑ ↓           pan the model (free look mode)
  Space         toggle speaking (lip-sync test)
  P             toggle the always-on Scene1 idle animation
  + / -         scale the model (free look mode)
  B             trigger a manual blink
  R             reset the framing (the frame in frame mode)
  Ctrl+S        save the framing (the frame in frame mode)
  Z             toggle the zone editor
  F             toggle the frame editor
  [ ] , .       grow / shrink the selected zone (zones mode)
  X             save the zones
  Escape        quit

The right panel is scrollable.  The status bar shows the current mode, the
frame and the values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── OpenGL format MUST be set before QApplication ────────────────────────────
try:
    from live2d_face import configure_opengl_format, Live2DWidget
    configure_opengl_format()
except ImportError as e:
    print(f"[ERROR] live2d-py or live2d_face.py not found: {e}")
    print("Install with:  .venv\\Scripts\\pip install live2d-py")
    sys.exit(1)

from behavior_state import BehaviorState
from behavior_director import MotionBudget

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QSlider, QStatusBar, QPushButton, QGroupBox, QScrollArea,
    QComboBox,
)
from PySide6.QtGui import QKeyEvent, QWheelEvent, QMouseEvent, QPainter, QPen, QColor

# Feature 13 touch-reaction zones + Feature 15 model frame
# (config/touch_reactions.json / config/live2d.json).  Optional — the preview
# still works without the modules (only plain pan/zoom then).
try:
    from core.touch import (                                 # noqa: E402
        get_config as get_touch_config,
        zone_rect_model as touch_zone_rect_model,
            )
    from core.framing import Frame, Framing                 # noqa: E402
    _TOUCH_OK = True
except Exception as _touch_err:                              # pragma: no cover
    _TOUCH_OK = False
    print(f"[Preview] core.touch unavailable, zones editor disabled: {_touch_err}")

# ── Config paths ──────────────────────────────────────────────────────────────
LIVE2D_JSON  = ROOT / "config" / "live2d.json"
PET_STATE    = ROOT / "config" / "pet_state.json"
TOUCH_JSON   = ROOT / "config" / "touch_reactions.json"
MOODS_JSON   = ROOT / "config" / "live2d_moods.json"
MODEL_JSON   = ROOT / "characters" / "marin" / "live2d" / "简.model3.json"


def _load_moods() -> list[str]:
    try:
        raw = json.loads(MOODS_JSON.read_text(encoding="utf-8"))
        return [k for k in raw if not k.startswith("_")]
    except Exception:
        return ["idle"]


def _load_live2d_cfg() -> dict:
    defaults = {"face": {"scale": 2.2, "offset_x": 0.0, "offset_y": 0.35},
                "body_control": True, "wink_swap": False,
                "look_invert_x": False,
                "behavior_director": True, "liveliness": 0.7,
                "lean_param": "ParamBodyAngleY0", "lean_sign": 1.0,
                "breath_pause_enabled": True,
                "breath_pause_every": [40.0, 90.0], "breath_pause_hold": [1.0, 1.5],
                "motion_budget": {"max_head_angle": 8.0, "max_eye_offset": 0.5,
                                  "max_body_drift": 2.0}}
    try:
        raw = json.loads(LIVE2D_JSON.read_text(encoding="utf-8"))
        defaults["face_renderer"] = raw.get("face_renderer", "sprites")
        defaults["face"].update(raw.get("face", {}))
        defaults["body_control"] = bool(raw.get("body_control", True))
        defaults["wink_swap"] = bool(raw.get("wink_swap", False))
        defaults["look_invert_x"] = bool(raw.get("look_invert_x", False))
        defaults["behavior_director"] = bool(raw.get("behavior_director", True))
        defaults["liveliness"] = float(raw.get("liveliness", 0.7))
        defaults["lean_param"] = raw.get("lean_param", defaults["lean_param"])
        defaults["lean_sign"] = float(raw.get("lean_sign", defaults["lean_sign"]))
        defaults["breath_pause_enabled"] = bool(raw.get("breath_pause_enabled", True))
        defaults["breath_pause_every"] = raw.get("breath_pause_every", defaults["breath_pause_every"])
        defaults["breath_pause_hold"] = raw.get("breath_pause_hold", defaults["breath_pause_hold"])
        if isinstance(raw.get("motion_budget"), dict):
            defaults["motion_budget"].update(raw["motion_budget"])
    except Exception:
        pass
    return defaults


class DraggableCanvas(QWidget):
    """Thin wrapper around Live2DWidget that intercepts mouse drag and scroll
    for pan/zoom, then forwards the framing change back to PreviewWindow via
    a callback so sliders stay in sync.

    Touch-zone editing (Feature 13): when ``zones_mode`` is ON the canvas
    routes press/move/wheel to the zone editor handlers provided by
    PreviewWindow instead of the normal pan/zoom."""

    def __init__(self, live2d_widget: "Live2DWidget", on_change, parent=None):
        super().__init__(parent)
        self._lw = live2d_widget
        self._on_change = on_change   # callable(scale, offset_x, offset_y)
        self._drag_start: QPoint | None = None
        self._drag_ox = 0.0
        self._drag_oy = 0.0

        # Touch-zone editing providers (set by PreviewWindow):
        self.zones_mode = False
        self.zones_rects = None       # callable() -> {name: (l, t, r, b)} px
        self.zones_select = None      # callable(name)
        self.zones_translate = None   # callable(name, du, dv)  (model units)
        self.zones_cycle = None       # callable()
        self.pressed_zone: str | None = None
        self._zone_last: QPoint | None = None

        # Feature 15: model-frame editing (move / resize the pet's crop)
        self.frame_mode = False
        self.frame_get = None         # callable() -> Frame
        self.frame_set = None         # callable(frame)
        self.frame_zoom = None        # callable(factor)
        self._frame_drag: str | None = None   # "move" | "resize"
        self._frame_last: QPoint | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._lw)

        self.setMouseTracking(True)
        self._lw.setMouseTracking(True)
        # Install event filter on the child so we catch its mouse events too
        self._lw.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._lw:
            # Touch-zone edit mode wins over the normal pan/zoom routing.
            if self.zones_mode:
                t = event.type()
                if t == event.Type.MouseButtonPress:
                    self._zone_press(event)
                    return True
                if t == event.Type.MouseMove:
                    self._zone_move(event)
                    return True
                if t == event.Type.MouseButtonRelease:
                    self.pressed_zone = None
                    self._lw.setCursor(Qt.OpenHandCursor)
                    return True
                if t == event.Type.Wheel:
                    self._zone_wheel(event)
                    return True
            # Feature 15: model-frame editing.
            if self.frame_mode:
                t = event.type()
                if t == event.Type.MouseButtonPress:
                    self._frame_press(event)
                    return True
                if t == event.Type.MouseMove:
                    self._frame_move(event)
                    return True
                if t == event.Type.MouseButtonRelease:
                    self._frame_drag = None
                    self._lw.setCursor(Qt.OpenHandCursor)
                    return True
                if t == event.Type.Wheel:
                    self._frame_wheel(event)
                    return True
            if event.type() == event.Type.MouseButtonPress:
                self._handle_press(event)
                return True
            if event.type() == event.Type.MouseMove:
                self._handle_move(event)
                return True
            if event.type() == event.Type.MouseButtonRelease:
                self._drag_start = None
                self._lw.setCursor(Qt.ArrowCursor)
                return True
            if event.type() == event.Type.Wheel:
                self._handle_wheel(event)
                return True
        return super().eventFilter(obj, event)

    def _handle_press(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._drag_start = e.position().toPoint()
            self._drag_ox = self._lw.get_model_offset()[0]
            self._drag_oy = self._lw.get_model_offset()[1]
            self._lw.setCursor(Qt.ClosedHandCursor)

    def _handle_move(self, e: QMouseEvent):
        if self._drag_start is None:
            return
        if not (e.buttons() & Qt.LeftButton):
            self._drag_start = None
            self._lw.setCursor(Qt.ArrowCursor)
            return
        delta = e.position().toPoint() - self._drag_start
        # Feature 15: the runtime's MEASURED pixels-per-model-unit (not a
        # guessed constant), so the model follows the cursor exactly.
        px_per_unit = self._lw.px_per_unit() or 1.0
        new_x = self._drag_ox + delta.x() / px_per_unit
        new_y = self._drag_oy - delta.y() / px_per_unit  # Y inverted
        new_x = max(-50.0, min(50.0, new_x))
        new_y = max(-50.0, min(50.0, new_y))
        self._lw.set_model_offset(new_x, new_y)
        self._on_change(self._lw.get_model_scale(), new_x, new_y)

    def _handle_wheel(self, e: QWheelEvent):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        factor = 1.05 if dy > 0 else 1.0 / 1.05
        new_scale = max(0.1, min(20.0, self._lw.get_model_scale() * factor))
        self._lw.set_model_scale(new_scale)
        ox, oy = self._lw.get_model_offset()
        self._on_change(new_scale, ox, oy)

    # ── Touch-zone editing (Feature 13) ───────────────────────────────────────

    def _zone_ppu(self) -> float:
        """Measured pixels per model unit (exact, from the runtime)."""
        pu = self._lw.px_per_unit()
        if pu and pu > 0.0:
            return pu
        return max(1.0, self._lw.width() * self._lw.get_model_scale() / 2.0)

    def _zone_press(self, e: QMouseEvent) -> None:
        boxes = self.zones_rects() or {}
        pos = e.position()
        hit = None
        for name in ("face", "head", "shoulders"):    # most specific first
            rect = boxes.get(name)
            if rect is None:
                continue
            l, t, r, b = rect
            if l <= pos.x() <= r and t <= pos.y() <= b:
                hit = name
                break
        if hit is None and boxes:                     # nearest centre wins
            best_name, best_d = None, None
            for name, (l, t, r, b) in boxes.items():
                cx, cy = (l + r) / 2.0, (t + b) / 2.0
                d = (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2
                if best_d is None or d < best_d:
                    best_name, best_d = name, d
            hit = best_name
        self.pressed_zone = hit
        self._zone_last = pos
        if hit and self.zones_select:
            self.zones_select(hit)
        self._lw.setCursor(Qt.ClosedHandCursor)

    def _zone_move(self, e: QMouseEvent) -> None:
        if self.pressed_zone is None or self._zone_last is None:
            return
        if not (e.buttons() & Qt.LeftButton):
            self.pressed_zone = None
            self._lw.setCursor(Qt.OpenHandCursor)
            return
        pos = e.position()
        du = (pos.x() - self._zone_last.x()) / self._zone_ppu()
        dv = -(pos.y() - self._zone_last.y()) / self._zone_ppu()
        self._zone_last = pos
        if self.zones_translate:
            self.zones_translate(self.pressed_zone, du, dv)

    def _zone_wheel(self, e: QWheelEvent) -> None:
        if self.zones_cycle:
            self.zones_cycle()

    # ── Model-frame editing (Feature 15) ──────────────────────────────────────

    _HANDLE = 18        # px: the resize handle in the bottom-right corner

    def _frame_press(self, e: QMouseEvent) -> None:
        pos = e.position()
        w = self._lw.width()
        h = self._lw.height()
        on_handle = (pos.x() >= w - self._HANDLE and pos.y() >= h - self._HANDLE)
        self._frame_drag = "resize" if on_handle else "move"
        self._frame_last = pos
        self._lw.setCursor(Qt.SizeFDiagCursor if on_handle
                           else Qt.ClosedHandCursor)

    def _frame_move(self, e: QMouseEvent) -> None:
        if self._frame_drag is None or self._frame_last is None:
            return
        if not (e.buttons() & Qt.LeftButton):
            self._frame_drag = None
            self._lw.setCursor(Qt.OpenHandCursor)
            return
        pos = e.position()
        pu = self._lw.px_per_unit() or 1.0
        dx = (pos.x() - self._frame_last.x()) / pu
        dy = (pos.y() - self._frame_last.y()) / pu
        self._frame_last = pos
        if self._frame_drag == "move" and self.frame_get and self.frame_set:
            # dragging the model right moves the visible region left
            frame = self.frame_get()
            if frame is not None:
                self.frame_set(frame.moved(-dx, dy))
        elif self._frame_drag == "resize" and self.frame_get and self.frame_set:
            # dragging the corner changes the frame's HEIGHT -> the pet's
            # window becomes taller/shorter (her body is cropped vertically)
            frame = self.frame_get()
            if frame is not None and frame.w > 0.0:
                span = max(1.0, float(self._lw.height()))
                new_h = frame.h + (dy - dx) * 2.0 / span * frame.h
                new_h = max(frame.w * 0.2, min(frame.w * 6.0, new_h))
                self.frame_set(frame.resized(frame.w, new_h))

    def _frame_wheel(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if dy == 0 or not self.frame_zoom:
            return
        self.frame_zoom(1.05 if dy > 0 else 1.0 / 1.05)


class TouchZonesOverlay(QWidget):
    """Feature 13/15: draws the touch zones (and, in frame mode, the model
    frame border + resize handle) over the preview canvas.

    Pure visualization (transparent for mouse; DraggableCanvas's event filter
    drives editing).  Rectangles come from a supplier callback so the window
    recomputes them from its own in-edit zone state each paint — resize /
    zoom / pan is followed automatically."""

    _COLORS = {
        "head": QColor(255, 168, 60),
        "face": QColor(110, 200, 255),
        "shoulders": QColor(160, 255, 140),
    }

    def __init__(self, rects_supplier, selected_supplier, parent=None,
                 frame_mode_supplier=None, frame_rect_supplier=None):
        super().__init__(parent)
        self._rects_supplier = rects_supplier
        self._selected_supplier = selected_supplier
        self._frame_mode_supplier = frame_mode_supplier
        self._frame_rect_supplier = frame_rect_supplier
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")

    def refresh(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.update()

    def paintEvent(self, ev):
        boxes = self._rects_supplier() or {}
        selected = self._selected_supplier()
        painter = QPainter(self)
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        for name, (l, t, r, b) in boxes.items():
            color = QColor(self._COLORS.get(name, QColor(240, 240, 240)))
            fill = QColor(color)
            fill.setAlpha(34 if name != selected else 72)
            painter.setPen(QPen(color, 3 if name == selected else 2))
            painter.setBrush(fill)
            painter.drawRect(l, t, r - l, b - t)
            painter.setPen(Qt.white)
            painter.drawText(int(l) + 6, int(t) + 16, name)

        # Feature 15: the frame border (the shape of the pet's window) and
        # the resize handle in its bottom-right corner.  The border follows
        # the MODEL widget, not the whole canvas - in frame mode the widget
        # has the frame's aspect, so this IS the pet's window shape.
        if (self._frame_mode_supplier is not None
                and self._frame_mode_supplier()):
            rect = None
            if self._frame_rect_supplier is not None:
                rect = self._frame_rect_supplier()
            if rect is None:
                rect = (0, 0, self.width(), self.height())
            x, y, w, h = rect
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(x + 2, y + 2, w - 4, h - 4)
            painter.setPen(QPen(QColor(120, 220, 255), 3))
            painter.drawRect(x + w - 20, y + h - 20, 18, 18)


class PreviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Live2D Preview & Tuning — Evelyn")
        self.setMinimumSize(800, 600)

        self._moods  = _load_moods()
        self._mood_i = 0
        self._speaking = False

        cfg = _load_live2d_cfg()
        face = cfg["face"]
        self._scale    = float(face.get("scale",    2.2))
        self._offset_x = float(face.get("offset_x", 0.0))
        self._offset_y = float(face.get("offset_y", 0.35))

        # ── Live2D widget + draggable canvas wrapper ──────────────────────────
        try:
            from body_tags import actions as _body_actions
        except Exception:
            _body_actions = None
        self._body_actions = _body_actions() if _body_actions else None
        max_fx = 3
        if self._body_actions is not None:
            max_fx = int(getattr(self._body_actions, "max_simultaneous_fx", 3))
        # Phase 2b: a shared BehaviorState + behavior director so the preview can
        # verify that a sad / low-arousal / low-engagement state moves less.
        mb = cfg.get("motion_budget", {}) or {}
        self.behavior_state = BehaviorState()
        self._liveliness = float(cfg.get("liveliness", 0.7))
        self._lean_sign = float(cfg.get("lean_sign", 1.0))
        self._live2d = Live2DWidget(
            MODEL_JSON, MOODS_JSON, parent=None,
            body_enabled=bool(cfg.get("body_control", True)),
            wink_swap=bool(cfg.get("wink_swap", False)),
            look_invert_x=bool(cfg.get("look_invert_x", False)),
            max_simultaneous_fx=max_fx,
            behavior_director_enabled=bool(cfg.get("behavior_director", True)),
            behavior_state=self.behavior_state,
            motion_budget=MotionBudget(
                max_head_angle=float(mb.get("max_head_angle", 8.0)),
                max_eye_offset=float(mb.get("max_eye_offset", 0.5)),
                max_body_drift=float(mb.get("max_body_drift", 2.0)),
            ),
            liveliness=self._liveliness,
            lean_param=cfg.get("lean_param", "ParamBodyAngleY0"),
            lean_sign=float(cfg.get("lean_sign", 1.0)),
            breath_pause_enabled=bool(cfg.get("breath_pause_enabled", True)),
            breath_pause_every=tuple(cfg.get("breath_pause_every", [40.0, 90.0])),
            breath_pause_hold=tuple(cfg.get("breath_pause_hold", [1.0, 1.5])),
        )
        self._live2d.setMinimumSize(450, 450)
        self._live2d.set_model_scale(self._scale)
        self._live2d.set_model_offset(self._offset_x, self._offset_y)
        self._live2d.setCursor(Qt.OpenHandCursor)

        self._canvas = DraggableCanvas(self._live2d, self._on_mouse_framing)

        # ── Feature 13/15: touch-zone + model-frame editor state ──────────────
        self._zones_mode = False
        self._zones_overlay = None
        self._zone_state: dict = {}          # name -> (x, y, w, h) MODEL units
        self._zone_selected: str | None = "head"
        # Feature 15: the model frame (the pet square's crop, in model units)
        self._frame_mode = False
        self._frame = None

        # ── Layout ────────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        main_layout.addWidget(self._canvas, stretch=3)

        # Right panel.  It is SCROLLABLE: there are seven group boxes and they
        # do not fit into a 600 px tall window - without the scroll area the
        # lower ones (behavior state, lean) were simply unreachable.
        panel = QWidget()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(360)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setSpacing(8)
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setWidget(panel)
        panel_scroll.setMinimumWidth(320)
        main_layout.addWidget(panel_scroll, stretch=1)

        # Mood list
        mood_box = QGroupBox("Mood  (← →  or  1-0)")
        mood_inner = QVBoxLayout(mood_box)
        self._mood_label = QLabel(self._moods[0])
        self._mood_label.setAlignment(Qt.AlignCenter)
        self._mood_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        mood_inner.addWidget(self._mood_label)
        btn_row = QHBoxLayout()
        prev_btn = QPushButton("◀ Prev")
        next_btn = QPushButton("Next ▶")
        prev_btn.clicked.connect(self._prev_mood)
        next_btn.clicked.connect(self._next_mood)
        btn_row.addWidget(prev_btn)
        btn_row.addWidget(next_btn)
        mood_inner.addLayout(btn_row)
        speak_btn = QPushButton("Toggle Speaking  [Space]")
        speak_btn.setCheckable(True)
        speak_btn.toggled.connect(self._toggle_speaking)
        mood_inner.addWidget(speak_btn)

        scene1_btn = QPushButton("⏸ Idle anim: ON  [P]")
        scene1_btn.clicked.connect(self._toggle_scene1)
        mood_inner.addWidget(scene1_btn)
        self._scene1_btn = scene1_btn

        panel_layout.addWidget(mood_box)

        # Framing sliders (the LEGACY scale/offset - the "default framing"
        # the pet resets to).  In frame mode the frame controls the framing,
        # so these are disabled there (see _toggle_frame_mode).
        framing_box = QGroupBox("Default framing  (scale / offset)")
        framing_inner = QVBoxLayout(framing_box)

        framing_inner.addWidget(QLabel("Scale"))
        self._scale_slider = self._make_slider(50, 1000, int(self._scale * 100))
        self._scale_slider.valueChanged.connect(self._on_scale_slider)
        framing_inner.addWidget(self._scale_slider)

        framing_inner.addWidget(QLabel("Offset X"))
        self._ox_slider = self._make_slider(-300, 300, int(self._offset_x * 100))
        self._ox_slider.valueChanged.connect(self._on_ox_slider)
        framing_inner.addWidget(self._ox_slider)

        framing_inner.addWidget(QLabel("Offset Y"))
        self._oy_slider = self._make_slider(-300, 300, int(self._offset_y * 100))
        self._oy_slider.valueChanged.connect(self._on_oy_slider)
        framing_inner.addWidget(self._oy_slider)

        self._framing_note = QLabel("The pet's RESET uses these values.")
        self._framing_note.setWordWrap(True)
        framing_inner.addWidget(self._framing_note)

        reset_btn = QPushButton("Reset framing  [R]")
        reset_btn.clicked.connect(self._reset_framing)
        framing_inner.addWidget(reset_btn)

        save_btn = QPushButton("💾 Save to live2d.json  [Ctrl+S]")
        save_btn.clicked.connect(self._save_framing)
        framing_inner.addWidget(save_btn)

        panel_layout.addWidget(framing_box)

        # ── Feature 13/15: touch zones + model frame (debug + edit) ─────────
        zones_box = QGroupBox("Touch zones  [Z]")
        zones_inner = QVBoxLayout(zones_box)
        zones_btn = QPushButton("Show / edit zones  [Z]")
        zones_btn.setCheckable(True)
        zones_btn.setEnabled(_TOUCH_OK)
        zones_btn.toggled.connect(self._toggle_zones_mode)
        zones_btn.setToolTip("Drag a zone on the model to move it.\n"
                             "Wheel = next zone,  [ ] = width,  , . = height,\n"
                             "X = save to config/touch_reactions.json")
        zones_inner.addWidget(zones_btn)
        self._zones_btn = zones_btn

        self._zones_label = QLabel("selected: -")
        self._zones_label.setWordWrap(True)
        zones_inner.addWidget(self._zones_label)

        zones_row = QHBoxLayout()
        for label, cb in (("W−", lambda: self._zone_nudge(-1.0, 0.0)),
                          ("W+", lambda: self._zone_nudge(1.0, 0.0)),
                          ("H−", lambda: self._zone_nudge(0.0, -1.0)),
                          ("H+", lambda: self._zone_nudge(0.0, 1.0))):
            b = QPushButton(label)
            b.clicked.connect(cb)
            b.setToolTip("Grow / shrink the selected zone")
            zones_row.addWidget(b)
        zones_inner.addLayout(zones_row)

        zones_hint = QLabel("drag = move · wheel = next zone\n"
                            "[ ] width · , . height · X saves")
        zones_hint.setWordWrap(True)
        zones_inner.addWidget(zones_hint)
        zones_save_btn = QPushButton("Save zones  [X]"
                                     if _TOUCH_OK else "zones unavailable")
        zones_save_btn.setEnabled(_TOUCH_OK)
        zones_save_btn.clicked.connect(self._save_zones)
        zones_save_btn.setToolTip("Writes the zones to config/touch_reactions.json")
        zones_inner.addWidget(zones_save_btn)
        panel_layout.addWidget(zones_box)

        # ── Feature 15: the model frame + the pet window size ───────────────
        frame_box = QGroupBox("Model frame  [F]")
        frame_inner = QVBoxLayout(frame_box)
        frame_btn = QPushButton("Edit model frame  [F]")
        frame_btn.setCheckable(True)
        frame_btn.toggled.connect(self._toggle_frame_mode)
        frame_btn.setToolTip("The frame is the model area the pet shows.\n"
                             "Drag = move it, bottom-right corner = height\n"
                             "(a taller frame = a taller window = vertical crop),\n"
                             "wheel = zoom.")
        frame_inner.addWidget(frame_btn)
        self._frame_btn = frame_btn

        self._frame_label = QLabel("frame: -")
        self._frame_label.setWordWrap(True)
        frame_inner.addWidget(self._frame_label)

        frame_row = QHBoxLayout()
        frame_save_btn = QPushButton("Save frame")
        frame_save_btn.clicked.connect(self._save_frame)
        frame_save_btn.setToolTip("Writes the frame to config/live2d.json")
        frame_row.addWidget(frame_save_btn)
        frame_reset_btn = QPushButton("Reset")
        frame_reset_btn.clicked.connect(self._reset_frame)
        frame_reset_btn.setToolTip("Back to the default framing")
        frame_row.addWidget(frame_reset_btn)
        frame_inner.addLayout(frame_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Pet width"))
        self._pet_size_slider = QSlider(Qt.Horizontal)
        self._pet_size_slider.setRange(200, 900)
        self._pet_size_slider.setValue(400)
        self._pet_size_slider.setToolTip(
            "The pet window's width in px (the height follows the frame)")
        self._pet_size_slider.valueChanged.connect(self._on_pet_size_slider)
        size_row.addWidget(self._pet_size_slider, stretch=1)
        self._pet_size_label = QLabel("400 px")
        self._pet_size_label.setMinimumWidth(52)
        self._pet_size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        size_row.addWidget(self._pet_size_label)
        pet_size_btn = QPushButton("Set")
        pet_size_btn.clicked.connect(
            lambda: self._save_pet_size(self._pet_size_slider.value()))
        pet_size_btn.setToolTip("Writes the pet size to config/pet_state.json")
        size_row.addWidget(pet_size_btn)
        frame_inner.addLayout(size_row)
        panel_layout.addWidget(frame_box)

        # Body action test panel (fire fx/look/gesture/wink without the LLM)
        if self._body_actions is not None:
            body_box = QGroupBox("Test body action")
            body_inner = QVBoxLayout(body_box)
            for key, cfg in self._body_actions.enabled_capped_actions().items():
                btn = QPushButton(self._action_label(key, cfg))
                btn.clicked.connect(lambda _, k=key, c=cfg: self._fire_body_action(k, c))
                body_inner.addWidget(btn)
            if not self._body_actions.enabled_capped_actions():
                body_inner.addWidget(QLabel("(no enabled actions)"))
            body_scroll = QScrollArea()
            body_scroll.setWidgetResizable(True)
            body_scroll.setWidget(body_box)
            body_scroll.setMinimumHeight(120)
            body_scroll.setMaximumHeight(380)
            panel_layout.addWidget(body_scroll, stretch=1)

        # ── Behavior-state sliders (Phase 2b) ─────────────────────────────────
        # Directly drive valence/arousal/engagement + liveliness on the behavior
        # state so the director can be tuned/verified visually (a sad / low-
        # arousal state should visibly move slower and less).
        state_box = QGroupBox("Behavior state")
        state_inner = QVBoxLayout(state_box)

        state_inner.addWidget(QLabel("Valence  (-1 sad … +1 happy)"))
        self._valence_slider = self._make_slider(-100, 100, 0)
        self._valence_slider.valueChanged.connect(self._on_state_sliders)
        state_inner.addWidget(self._valence_slider)

        state_inner.addWidget(QLabel("Arousal  (0 calm … 1 excited)"))
        self._arousal_slider = self._make_slider(0, 100, 50)
        self._arousal_slider.valueChanged.connect(self._on_state_sliders)
        state_inner.addWidget(self._arousal_slider)

        state_inner.addWidget(QLabel("Engagement  (0 … 1)"))
        self._engagement_slider = self._make_slider(0, 100, 50)
        self._engagement_slider.valueChanged.connect(self._on_state_sliders)
        state_inner.addWidget(self._engagement_slider)

        self._liveliness_label = QLabel(f"Liveliness  (now {self._liveliness:.2f})")
        state_inner.addWidget(self._liveliness_label)
        self._liveliness_slider = self._make_slider(0, 100, int(self._liveliness * 100))
        self._liveliness_slider.valueChanged.connect(self._on_liveliness_slider)
        state_inner.addWidget(self._liveliness_slider)

        # Phase 2e: exact-value presets (Still/Calm/Normal/Lively).
        preset_row = QHBoxLayout()
        for label, val in [("Still 0", 0.0), ("Calm .4", 0.4),
                           ("Normal .7", 0.7), ("Lively 1", 1.0)]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, v=val: self._set_liveliness_preset(v))
            preset_row.addWidget(btn)
        state_inner.addLayout(preset_row)

        panel_layout.addWidget(state_box)

        # ── Phase 2c: lean param / sign + breath-pause toggles (preview tuning) ──
        lean_box = QGroupBox("Lean  (try which looks like leaning in)")
        lean_inner = QVBoxLayout(lean_box)
        lean_inner.addWidget(QLabel("Lean param"))
        self._lean_combo = QComboBox()
        self._lean_combo.addItems(["ParamBodyAngleY0", "ParamChestAngleY",
                                   "ParamBodyAngleX0", "ParamBodyAngleZ0"])
        cur_lean = cfg.get("lean_param", "ParamBodyAngleY0")
        self._lean_combo.setCurrentText(cur_lean if self._lean_combo.findText(cur_lean) >= 0 else "ParamBodyAngleY0")
        self._lean_combo.currentTextChanged.connect(self._on_lean_param)
        lean_inner.addWidget(self._lean_combo)
        flip_btn = QPushButton(f"Lean sign  (now {'+' if cfg.get('lean_sign', 1.0) >= 0 else '-'}1)")
        flip_btn.clicked.connect(self._flip_lean_sign)
        lean_inner.addWidget(flip_btn)
        self._flip_lean_btn = flip_btn
        lean_inner.addWidget(QLabel("Switch to  B-L-Listen  to see the lean+nod"))
        panel_layout.addWidget(lean_box)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._update_status()

        # Apply first mood
        self._apply_mood()

        # Feature 13/15: the zones and the frame are known up front, so the
        # overlay can show them even before an editor is opened.
        self._frame = self._load_frame()
        self._load_zone_state()
        self._update_frame_label()

    # ── Behavior-state slider helpers (Phase 2b) ───────────────────────────────

    def _on_state_sliders(self) -> None:
        """Push the valence/arousal/engagement sliders into the behavior state so
        the director output changes live (verification aid)."""
        self.behavior_state.set_preview_axes(
            self._valence_slider.value() / 100.0,
            self._arousal_slider.value() / 100.0,
            self._engagement_slider.value() / 100.0,
        )

    def _on_liveliness_slider(self, val: int) -> None:
        self._liveliness = val / 100.0
        self._live2d.set_liveliness(self._liveliness)
        self._liveliness_label.setText(f"Liveliness  (now {self._liveliness:.2f})")
        self._update_status()

    def _set_liveliness_preset(self, val: float) -> None:
        """Snap to an exact liveliness preset (Phase 2e)."""
        self._liveliness = val
        self._liveliness_slider.blockSignals(True)
        self._liveliness_slider.setValue(int(val * 100))
        self._liveliness_slider.blockSignals(False)
        self._live2d.set_liveliness(val)
        self._liveliness_label.setText(f"Liveliness  (now {val:.2f})")
        self._update_status()

    def _on_lean_param(self, name: str) -> None:
        """Try a different lean param live (Phase 2c)."""
        self._live2d.set_lean_param(name, self._lean_sign)

    def _flip_lean_sign(self) -> None:
        """Flip the lean direction live (Phase 2c)."""
        self._lean_sign = -self._lean_sign
        self._flip_lean_btn.setText(f"Lean sign  (now {'+' if self._lean_sign >= 0 else '-'}1)")
        self._live2d.set_lean_param(self._lean_combo.currentText(), self._lean_sign)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_slider(min_val: int, max_val: int, val: int) -> QSlider:
        s = QSlider(Qt.Horizontal)
        s.setRange(min_val, max_val)
        s.setValue(val)
        return s

    def _apply_mood(self) -> None:
        mood = self._moods[self._mood_i]
        self._mood_label.setText(mood)
        self._live2d.update_art(mood, self._speaking)
        # Print the mood's final target values (visual-tuning aid).
        targets = self._live2d._target
        values = {p: round(v, 3) for p, v in targets.items() if abs(v) > 1e-6}
        print(f"[Mood: {mood}] final params: {values}")

    @staticmethod
    def _action_label(key: str, cfg: dict) -> str:
        """Human label for a body action button (e.g. "fx:blush" → "Blush")."""
        kind, _, name = key.partition(":")
        icons = {"fx": "💢", "look": "👀", "gesture": "🤲", "wink": "😉"}
        icon = icons.get(kind, "•")
        pretty = name.replace("_", " ").capitalize()
        if kind == "wink" and name not in ("left", "right"):
            side = cfg.get("side", "")
            pretty = f"Wink ({side})" if side else "Wink"
        return f"{icon} {pretty}"

    def _fire_body_action(self, key: str, cfg: dict) -> None:
        """Fire a single body action directly on the Live2D widget (no LLM).

        ``cfg`` is already shaped by enabled_capped_actions() with kind/params or
        side, so we can pass it straight through the widget's apply_body_actions.
        """
        actions = [dict(cfg)]
        if cfg.get("kind") in ("fx", "look", "gesture"):
            actions[0]["duration"] = float(actions[0].get("duration", 0.9))
            actions[0]["params"] = dict(actions[0].get("params", {}) or {})
        self._live2d.apply_body_actions(actions)
        print(f"[Body] fired {key} -> apply_body_actions({actions})")
        self._status_bar.showMessage(f"Body action: {self._action_label(key, cfg)}", 2000)

    def _update_status(self) -> None:
        mode = "frame" if self._frame_mode else ("zones" if self._zones_mode
                                                 else "free look")
        frame_txt = "-"
        if self._frame is not None:
            f = self._frame
            frame_txt = f"x={f.x:.2f} y={f.y:.2f} w={f.w:.2f} h={f.h:.2f}"
        msg = (
            f"mode: {mode}  |  frame: {frame_txt}  |  "
            f"scale={self._scale:.2f}  offset=({self._offset_x:.3f}, "
            f"{self._offset_y:.3f})  |  "
            f"liveliness={self._liveliness:.2f}  lean={self._lean_combo.currentText()}, "
            f"sign={'+' if self._lean_sign >= 0 else '-'}1  |  mood: "
            f"{self._moods[self._mood_i]}"
        )
        self._status_bar.showMessage(msg)

    # ── Mood navigation ───────────────────────────────────────────────────────

    def _next_mood(self) -> None:
        self._mood_i = (self._mood_i + 1) % len(self._moods)
        self._apply_mood()
        self._update_status()

    def _prev_mood(self) -> None:
        self._mood_i = (self._mood_i - 1) % len(self._moods)
        self._apply_mood()
        self._update_status()

    def _toggle_speaking(self, on: bool) -> None:
        self._speaking = on
        self._live2d.update_art(self._moods[self._mood_i], on)

    def _toggle_scene1(self) -> None:
        """Toggle the always-on Scene1 idle animation (loops forever)."""
        new_state = not self._live2d.is_idle_animation_enabled()
        self._live2d.set_idle_animation_enabled(new_state)
        self._scene1_btn.setText(
            "⏸ Idle anim: ON  [P]" if new_state else "▶ Idle anim: OFF  [P]"
        )

    # ── Feature 13/15: touch-zone + model-frame editor ───────────────────────

    def _preview_framing(self):
        """The preview canvas seen through the CURRENT display mapping.

        This is measured from the runtime, so it is always correct - in
        frame mode it is the frame we are editing, otherwise it is whatever
        the preview's own framing shows."""
        w = float(self._live2d.width())
        h = float(self._live2d.height())
        frame = self._live2d.visible_frame(w, h)
        if frame is None:
            frame = self._frame
        if frame is None:
            return None
        return Framing(frame=frame, width_px=w, height_px=h)

    def _zone_rects(self) -> dict:
        """Zone rectangles in canvas px (the zones are MODEL-unit rects)."""
        if not (_TOUCH_OK and self._zone_state):
            return {}
        fr = self._preview_framing()
        if fr is None:
            return {}
        return {n: fr.rect_px(r) for n, r in self._zone_state.items()}

    def _load_zone_state(self) -> None:
        """Read the zones into MODEL units (frame-relative ones are converted).

        Called at startup so the overlay can show the zones even when only
        the frame editor is open."""
        if not _TOUCH_OK:
            return
        cfg = get_touch_config()
        self._zone_state = {}
        for name, rect in cfg.zones:
            if cfg.zones_relative:
                self._zone_state[name] = touch_zone_rect_model(
                    self._frame, rect, True) if self._frame else tuple(rect)
            else:
                self._zone_state[name] = tuple(rect)
        if self._zone_selected not in self._zone_state:
            self._zone_selected = next(iter(self._zone_state), None)
        self._update_zone_label()

    def _toggle_zones_mode(self, on: bool) -> None:
        if not _TOUCH_OK:
            self._zones_btn.blockSignals(True)
            self._zones_btn.setChecked(False)
            self._zones_btn.blockSignals(False)
            return
        self._zones_mode = bool(on)
        if self._zones_mode and self._frame_mode:
            # the two editors share the canvas - only one at a time
            self._frame_btn.blockSignals(True)
            self._frame_btn.setChecked(False)
            self._frame_btn.blockSignals(False)
            self._frame_mode = False
            self._canvas.frame_mode = False
        self._canvas.zones_mode = self._zones_mode
        if self._zones_mode and self._zones_overlay is None:
            self._zones_overlay = TouchZonesOverlay(
                self._zone_rects, lambda: self._zone_selected, self._canvas,
                frame_mode_supplier=lambda: self._frame_mode,
                frame_rect_supplier=self._frame_rect)
            self._canvas.zones_rects = self._zone_rects
            self._canvas.zones_select = self._zone_select
            self._canvas.zones_translate = self._zone_translate
            self._canvas.zones_cycle = self._zone_cycle
        if self._zones_mode:
            # The zones are edited in MODEL units so they stick to her body.
            # Frame-relative zones are converted once, here.
            self._load_zone_state()
        self._refresh_overlay()
        self._zones_btn.setText("Hide zones  [Z]" if self._zones_mode
                                else "Show / edit zones  [Z]")
        self._update_status()

    def _refresh_overlay(self) -> None:
        """The overlay is shared by the zone and frame editors - it is visible
        whenever EITHER editor is on (it used to vanish when zones were
        toggled off while the frame editor was still running)."""
        if self._zones_overlay is None:
            return
        show = self._zones_mode or self._frame_mode
        self._zones_overlay.setVisible(show)
        if show:
            self._zones_overlay.raise_()
            self._zones_overlay.refresh()

    def _zone_select(self, name: str) -> None:
        self._zone_selected = name
        self._update_zone_label()

    def _zone_translate(self, name: str, du: float, dv: float) -> None:
        if name not in self._zone_state:
            return
        x, y, w, h = self._zone_state[name]
        self._zone_state[name] = ((x + du), (y + dv), w, h)
        self._update_zone_label()
        if self._zones_overlay is not None:
            self._zones_overlay.update()

    def _zone_cycle(self) -> None:
        if not self._zone_state:
            return
        names = [n for n in ("face", "head", "shoulders") if n in self._zone_state]
        if not names:
            return
        cur = self._zone_selected if self._zone_selected in names else None
        idx = (names.index(cur) + 1) % len(names) if cur else 0
        self._zone_select(names[idx])

    def _zone_nudge(self, dw: float, dh: float) -> None:
        """Grow/shrink the selected zone.

        The steps are RELATIVE to the frame (2% of its width/height) - a
        fixed 0.05 model unit is invisible on a canvas that is tens of
        units wide."""
        if not (self._zone_selected and self._zone_selected in self._zone_state):
            return
        frame = self._frame
        if frame is None or frame.w <= 0.0:
            step = 0.5
        else:
            step = max(0.01, frame.w * 0.02)
        x, y, w, h = self._zone_state[self._zone_selected]
        new_w = max(frame.w * 0.02 if frame else 0.05,
                    w + dw * step)
        new_h = max(frame.h * 0.02 if frame else 0.05,
                    h + dh * step)
        self._zone_state[self._zone_selected] = (
            x - (new_w - w) / 2.0, y - (new_h - h) / 2.0, new_w, new_h)
        self._update_zone_label()
        if self._zones_overlay is not None:
            self._zones_overlay.update()

    def _update_zone_label(self) -> None:
        if self._zone_selected and self._zone_selected in self._zone_state:
            x, y, w, h = self._zone_state[self._zone_selected]
            self._zones_label.setText(f"{self._zone_selected}:  "
                                      f"x={x:.2f} y={y:.2f} w={w:.2f} h={h:.2f}")

    def _save_zones(self) -> None:
        """Write the edited zones into config/touch_reactions.json as ABSOLUTE
        model units (zones_relative = false) so they stick to her body.
        Every other key is preserved."""
        if not self._zone_state:
            return
        cfg_path = TOUCH_JSON
        try:
            raw: dict = {}
            if cfg_path.exists():
                raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            zones = raw.setdefault("zones", {})
            for name, (x, y, w, h) in self._zone_state.items():
                zones.setdefault(name, {}).update({
                    "x": round(x, 3), "y": round(y, 3),
                    "w": round(w, 3), "h": round(h, 3),
                })
            raw["zones"] = zones
            raw["zones_relative"] = False
            cfg_path.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self._status_bar.showMessage("Touch zones saved to "
                                         "config/touch_reactions.json", 5000)
        except Exception as e:
            self._status_bar.showMessage(f"Zones save failed: {e}", 5000)

    # ── Feature 15: the model frame editor ───────────────────────────────────

    def _load_frame(self):
        """The frame from config/live2d.json, or the one the runtime shows."""
        frame = Frame.from_dict(_load_live2d_cfg()["face"].get("frame"))
        if frame is None:
            frame = self._live2d.visible_frame(float(self._live2d.width()),
                                               float(self._live2d.height()))
        return frame

    def _toggle_frame_mode(self, on: bool) -> None:
        self._frame_mode = bool(on)
        self._canvas.frame_mode = self._frame_mode
        if self._frame_mode:
            if self._zones_mode:
                # the two editors share the canvas - only one at a time
                self._zones_btn.blockSignals(True)
                self._zones_btn.setChecked(False)
                self._zones_btn.blockSignals(False)
                self._zones_mode = False
                self._canvas.zones_mode = False
            if self._frame is None:
                self._frame = self._load_frame()
            if self._frame is not None:
                # show EXACTLY what the pet's window shows (same aspect)
                self._frame_set(self._frame)
            if self._zones_overlay is None:
                self._zones_overlay = TouchZonesOverlay(
                    self._zone_rects, lambda: self._zone_selected, self._canvas,
                    frame_mode_supplier=lambda: self._frame_mode,
                    frame_rect_supplier=self._frame_rect)
            self._canvas.frame_get = lambda: self._frame
            self._canvas.frame_set = self._frame_set
            self._canvas.frame_zoom = self._frame_zoom
            # the legacy framing sliders would fight the frame
            for s in (self._scale_slider, self._ox_slider, self._oy_slider):
                s.setEnabled(False)
            self._framing_note.setText(
                "Disabled in frame mode - the frame controls the framing.")
        else:
            for s in (self._scale_slider, self._ox_slider, self._oy_slider):
                s.setEnabled(True)
            self._framing_note.setText("The pet's RESET uses these values.")
            # undo the fixed size the frame mode imposed on the model widget
            self._live2d.setMinimumSize(450, 450)
            self._live2d.setMaximumSize(16777215, 16777215)
            self._live2d.resize(450, 450)
        self._refresh_overlay()
        self._frame_btn.setText("Hide frame editor  [F]" if self._frame_mode
                                else "Edit model frame  [F]")
        self._update_frame_label()
        self._update_status()

    def _frame_rect(self):
        """The model's rectangle inside the canvas - in frame mode this IS
        the shape of the pet's window (the widget has the frame's aspect)."""
        return (0, 0, int(self._live2d.width()), int(self._live2d.height()))

    def _frame_set(self, frame) -> None:
        """Move / resize the frame and re-render the model accordingly.

        The preview's model widget is given the FRAME's aspect, so what you
        see here is the shape of the pet's window (a taller frame = a taller
        window = her body cropped vertically)."""
        if frame is None:
            return
        self._frame = frame
        w = max(1, self._live2d.width())
        h = max(1, int(round(w * (frame.h / frame.w)))) if frame.w > 0 else w
        self._live2d.setFixedSize(w, h)
        self._live2d.apply_frame(frame, float(w), float(h))
        self._update_frame_label()
        if self._zones_overlay is not None:
            self._zones_overlay.update()

    def _frame_zoom(self, factor: float) -> None:
        if self._frame is not None:
            self._frame_set(self._frame.zoomed(factor))

    def _update_frame_label(self) -> None:
        if self._frame is None:
            self._frame_label.setText("frame: -")
            return
        f = self._frame
        self._frame_label.setText(
            f"frame: x={f.x:.2f} y={f.y:.2f} w={f.w:.2f} h={f.h:.2f}")

    def _save_frame(self) -> None:
        """Write the frame into config/live2d.json (everything else kept)."""
        if self._frame is None:
            return
        try:
            raw: dict = {}
            if LIVE2D_JSON.exists():
                raw = json.loads(LIVE2D_JSON.read_text(encoding="utf-8"))
            face = raw.get("face") if isinstance(raw.get("face"), dict) else {}
            face["frame"] = self._frame.as_dict()
            raw["face"] = face
            LIVE2D_JSON.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self._status_bar.showMessage(
                "Model frame saved to config/live2d.json", 5000)
        except Exception as e:
            self._status_bar.showMessage(f"Frame save failed: {e}", 5000)

    def _reset_frame(self) -> None:
        """Back to the default framing (the legacy scale/offset)."""
        face = _load_live2d_cfg()["face"]
        self._live2d.set_model_scale(float(face.get("scale", 2.2)))
        self._live2d.set_model_offset(float(face.get("offset_x", 0.0)),
                                      float(face.get("offset_y", 0.35)))
        frame = self._live2d.visible_frame(float(self._live2d.width()),
                                           float(self._live2d.height()))
        if frame is not None:
            self._frame_set(frame)
            self._save_frame()

    def _on_pet_size_slider(self, val: int) -> None:
        self._pet_size_label.setText(f"{val} px")

    def _save_pet_size(self, size: int) -> None:
        """The pet's face-square size (a per-machine override) → pet_state."""
        try:
            raw: dict = {}
            if PET_STATE.exists():
                raw = json.loads(PET_STATE.read_text(encoding="utf-8"))
            raw["face_size"] = int(size)
            PET_STATE.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self._status_bar.showMessage(
                f"Pet window size set to {size} px (config/pet_state.json)", 5000)
        except Exception as e:
            self._status_bar.showMessage(f"Pet size save failed: {e}", 5000)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._zones_overlay is not None and (self._zones_mode
                                                or self._frame_mode):
            self._zones_overlay.refresh()
        # frame mode: the model widget follows the canvas' shape
        if self._frame_mode and self._frame is not None:
            self._frame_set(self._frame)

    def _on_mouse_framing(self, scale: float, ox: float, oy: float) -> None:
        """Called by DraggableCanvas when drag/scroll changes framing.
        Updates internal state and syncs sliders without triggering their signals."""
        self._scale    = scale
        self._offset_x = ox
        self._offset_y = oy
        self._sync_sliders()
        self._update_status()

    def _sync_sliders(self) -> None:
        """Push current framing values into sliders without firing valueChanged."""
        for slider, val in (
            (self._scale_slider, int(self._scale    * 100)),
            (self._ox_slider,    int(self._offset_x * 100)),
            (self._oy_slider,    int(self._offset_y * 100)),
        ):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)

    # ── Framing ───────────────────────────────────────────────────────────────

    def _on_scale_slider(self, val: int) -> None:
        self._scale = val / 100.0
        self._live2d.set_model_scale(self._scale)
        self._update_status()

    def _on_ox_slider(self, val: int) -> None:
        self._offset_x = val / 100.0
        self._live2d.set_model_offset(self._offset_x, self._offset_y)
        self._update_status()

    def _on_oy_slider(self, val: int) -> None:
        self._offset_y = val / 100.0
        self._live2d.set_model_offset(self._offset_x, self._offset_y)
        self._update_status()

    def _reset_framing(self) -> None:
        """Reset the framing.  In frame mode the FRAME is reset (that is what
        the pet actually uses); otherwise the legacy scale/offset."""
        if self._frame_mode:
            self._reset_frame()
            return
        cfg = _load_live2d_cfg()["face"]
        self._scale    = float(cfg.get("scale",    2.2))
        self._offset_x = float(cfg.get("offset_x", 0.0))
        self._offset_y = float(cfg.get("offset_y", 0.35))
        self._sync_sliders()
        self._live2d.set_model_scale(self._scale)
        self._live2d.set_model_offset(self._offset_x, self._offset_y)
        self._update_status()

    def _save_framing(self) -> None:
        """Persist the framing back into config/live2d.json.

        In frame mode the FRAME is what the pet uses, so that is what gets
        saved; otherwise the legacy scale/offsets."""
        if self._frame_mode:
            self._save_frame()
            return
        try:
            raw: dict = {}
            if LIVE2D_JSON.exists():
                raw = json.loads(LIVE2D_JSON.read_text(encoding="utf-8"))
            face = raw.setdefault("face", {})
            face["scale"]    = round(self._scale,    3)
            face["offset_x"] = round(self._offset_x, 3)
            face["offset_y"] = round(self._offset_y, 3)
            LIVE2D_JSON.write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
            self._status_bar.showMessage(
                f"Saved → scale={self._scale:.3f}  offset_x={self._offset_x:.3f}  "
                f"offset_y={self._offset_y:.3f}", 4000
            )
            print(f"[Preview] Saved framing: scale={self._scale}  "
                  f"offset_x={self._offset_x}  offset_y={self._offset_y}")
        except Exception as e:
            self._status_bar.showMessage(f"Save failed: {e}", 4000)

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        mods = e.modifiers()

        # Digit keys 1-9, 0 → jump to mood index
        if Qt.Key_1 <= key <= Qt.Key_9:
            idx = key - Qt.Key_1
            if idx < len(self._moods):
                self._mood_i = idx
                self._apply_mood()
                self._update_status()
        elif key == Qt.Key_0:
            idx = 9
            if idx < len(self._moods):
                self._mood_i = idx
                self._apply_mood()
                self._update_status()

        elif key == Qt.Key_Right:
            self._next_mood()
        elif key == Qt.Key_Left:
            self._prev_mood()

        elif key == Qt.Key_Space:
            self._speaking = not self._speaking
            self._live2d.update_art(self._moods[self._mood_i], self._speaking)
            self._update_status()

        elif key == Qt.Key_B:
            # Manual blink trigger (hack: set blink state)
            self._live2d._blink_state = "closing"
            self._live2d._blink_t = 0.0

        elif key == Qt.Key_P:
            self._toggle_scene1()

        elif key == Qt.Key_Plus or key == Qt.Key_Equal:
            if self._frame_mode:
                self._frame_zoom(1.05)
            else:
                self._scale *= 1.05
                self._live2d.set_model_scale(self._scale)
                self._sync_sliders()
                self._update_status()

        elif key == Qt.Key_Minus:
            if self._frame_mode:
                self._frame_zoom(1.0 / 1.05)
            else:
                self._scale = max(0.1, self._scale * 0.95)
                self._live2d.set_model_scale(self._scale)
                self._sync_sliders()
                self._update_status()

        elif key == Qt.Key_Up:
            if self._frame_mode:
                if self._frame is not None:
                    self._frame_set(self._frame.moved(0.0, 0.05 * self._frame.h))
            else:
                self._offset_y = min(3.0, self._offset_y + 0.05)
                self._live2d.set_model_offset(self._offset_x, self._offset_y)
                self._sync_sliders()
                self._update_status()

        elif key == Qt.Key_Down:
            if self._frame_mode:
                if self._frame is not None:
                    self._frame_set(self._frame.moved(0.0, -0.05 * self._frame.h))
            else:
                self._offset_y = max(-3.0, self._offset_y - 0.05)
                self._live2d.set_model_offset(self._offset_x, self._offset_y)
                self._sync_sliders()
                self._update_status()

        elif key == Qt.Key_R:
            self._reset_framing()

        elif key == Qt.Key_S and (mods & Qt.ControlModifier):
            self._save_framing()

        elif key == Qt.Key_Z and self._zones_btn.isEnabled():
            self._zones_btn.toggle()          # emits toggled → _toggle_zones_mode

        elif key == Qt.Key_F:
            self._frame_btn.toggle()          # emits toggled → _toggle_frame_mode

        elif key == Qt.Key_BracketLeft and self._zones_mode:
            self._zone_nudge(-1.0, 0.0)
        elif key == Qt.Key_BracketRight and self._zones_mode:
            self._zone_nudge(1.0, 0.0)
        elif key == Qt.Key_Comma and self._zones_mode:
            self._zone_nudge(0.0, -1.0)
        elif key == Qt.Key_Period and self._zones_mode:
            self._zone_nudge(0.0, 1.0)
        elif key == Qt.Key_X and self._zones_mode:
            self._save_zones()

        elif key == Qt.Key_Escape:
            self.close()

        else:
            super().keyPressEvent(e)

    def closeEvent(self, e) -> None:
        self._live2d.dispose()
        super().closeEvent(e)


def main() -> None:
    # QSurfaceFormat set by configure_opengl_format() at module top
    app = QApplication(sys.argv)
    app.setApplicationName("Live2D Preview")

    win = PreviewWindow()
    win.show()

    ret = app.exec()
    sys.exit(ret)


if __name__ == "__main__":
    main()
