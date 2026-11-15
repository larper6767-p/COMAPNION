# -*- coding: utf-8 -*-
"""Minimal Live2D smoke test for live2d-py 1.0.0 + PySide6.

Purpose: find out whether the model renders and ANIMATES on this machine,
independent of the Evelyn app. Based on the official examples/main_pyside6.py.

Run from the project folder (put this file in tools/ or the project root):

    .venv\\Scripts\\python test_live2d_minimal.py
    .venv\\Scripts\\python test_live2d_minimal.py --canvas        (use the Canvas FBO like the app)
    .venv\\Scripts\\python test_live2d_minimal.py --app-format    (use the same GL format as the app)
    .venv\\Scripts\\python test_live2d_minimal.py --model "C:\\path\\to\\简.model3.json"

What you should see:
  * the model, with a transparent background
  * head swaying left/right (written by this script every frame)
  * blinking and breathing, hair/ears/tail moving (SDK auto effects + physics)
  * eyes following the mouse cursor
  * a console line every ~2 s with frame count and parameter values

PROOF TEST for the "model goes static" bug (run both, with --no-auto so the SDK
is not animating anything for you):

    .venv\\Scripts\\python test_live2d_minimal.py --no-auto --legacy-set
        -> head does NOT sway; console shows wrote=... read_back=0.0
    .venv\\Scripts\\python test_live2d_minimal.py --no-auto
        -> head sways; console shows read_back close to wrote

Controls: drag with left mouse button to move, right click or Esc to quit.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import traceback
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=None, help="path to the .model3.json")
ap.add_argument("--canvas", action="store_true", help="draw through live2d.utils.canvas.Canvas (what the app does)")
ap.add_argument("--app-format", action="store_true",
                help="use the app's QSurfaceFormat (OpenGL 2.1 compatibility, alpha, 4x MSAA)")
ap.add_argument("--no-write", action="store_true", help="do not write ParamAngleX (only SDK auto effects)")
ap.add_argument("--legacy-set", action="store_true",
                help="write with SetParamById (the OLD way; values get discarded by Update()'s LoadParameters)")
ap.add_argument("--no-auto", action="store_true",
                help="turn the SDK's auto blink/breath OFF (like the app does)")
args = ap.parse_args()

import live2d  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QCursor, QSurfaceFormat  # noqa: E402
from PySide6.QtOpenGLWidgets import QOpenGLWidget  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import PySide6  # noqa: E402


def find_model() -> Path:
    if args.model:
        return Path(args.model)
    here = Path(__file__).resolve().parent
    for root in (here, here.parent):
        p = root / "characters" / "marin" / "live2d" / "简.model3.json"
        if p.exists():
            return p
    raise SystemExit("Could not find 简.model3.json - pass --model PATH")


MODEL_JSON = find_model()


class Win(QOpenGLWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(500, 700)
        self.model = None
        self.canvas = None
        self.ok = False
        self.frames = 0
        self.t0 = time.monotonic()
        self.last_log = self.t0
        self._drag = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def initializeGL(self) -> None:
        try:
            from OpenGL import GL
            print("GL_VERSION  :", GL.glGetString(GL.GL_VERSION))
            print("GL_RENDERER :", GL.glGetString(GL.GL_RENDERER))
            print("GLSL        :", GL.glGetString(GL.GL_SHADING_LANGUAGE_VERSION))
            fmt = self.context().format()
            print("Context     : %d.%d profile=%s alpha=%d" % (
                fmt.majorVersion(), fmt.minorVersion(), fmt.profile().name, fmt.alphaBufferSize()))
        except Exception:
            print("(could not query GL info)")
            traceback.print_exc()
        try:
            live2d.glInit()
            self.model = live2d.Model()
            self.model.LoadModelJson(str(MODEL_JSON))
            self.model.Resize(self.width(), self.height())
            ids = self.model.GetParamIds()
            print("model loaded; parameters:", len(ids))
            for pid in ("ParamAngleX", "ParamBreath", "ParamEyeLOpen", "ParamMouthOpenY", "ParamSwitch2"):
                if pid in ids:
                    print("  %-16s min=%s max=%s default=%s" % (
                        pid,
                        self.model.GetParamMinById(pid),
                        self.model.GetParamMaxById(pid),
                        self.model.GetParamDefaultById(pid)))
                else:
                    print("  %-16s NOT on model" % pid)
            try:
                self.model.SetAutoBlink(not args.no_auto)
                self.model.SetAutoBreath(not args.no_auto)
            except Exception:
                print("(SetAutoBlink/SetAutoBreath unavailable)")
            if args.canvas:
                from live2d.utils.canvas import Canvas
                self.canvas = Canvas()
                dpr = self.devicePixelRatio()
                self.canvas.SetSize(int(self.width() * dpr), int(self.height() * dpr))
                print("Canvas created OK")
            self.ok = True
        except Exception:
            print("INIT FAILED:")
            traceback.print_exc()

    def resizeGL(self, w: int, h: int) -> None:
        if self.model is not None:
            dpr = self.devicePixelRatio()
            self.model.Resize(int(w * dpr), int(h * dpr))
            if self.canvas is not None:
                self.canvas.SetSize(int(w * dpr), int(h * dpr))

    def paintGL(self) -> None:
        if not self.ok:
            return
        try:
            t = time.monotonic() - self.t0
            self._wrote = 0.0
            if not args.no_write:
                self._wrote = 20.0 * math.sin(t * 1.2)
                if args.legacy_set:
                    # OLD way: live value only. Update() starts with LoadParameters(),
                    # which restores the saved values and throws this away.
                    self.model.SetParamById("ParamAngleX", self._wrote, 1.0)
                else:
                    # Writes the live value AND the saved value, so it survives Update().
                    self.model.SetSaveParamById("ParamAngleX", self._wrote, 1.0)
            # eyes follow the cursor (SDK drag): local logical coordinates
            p = self.mapFromGlobal(QCursor.pos())
            self.model.Drag(float(p.x()), float(p.y()))

            self.model.Update()

            if self.canvas is not None:
                def draw():
                    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
                    self.model.Draw()
                self.canvas.Draw(draw)
            else:
                live2d.clearBuffer()
                self.model.Draw()

            self.frames += 1
            now = time.monotonic()
            if now - self.last_log > 2.0:
                self.last_log = now
                print("frame %5d  fps~%.0f  AngleX wrote=%.1f read_back=%.1f  Breath=%.2f  EyeLOpen=%.2f" % (
                    self.frames, self.frames / max(now - self.t0, 1e-6),
                    self._wrote,
                    self.model.GetParamValueById("ParamAngleX"),
                    self.model.GetParamValueById("ParamBreath"),
                    self.model.GetParamValueById("ParamEyeLOpen")))
        except Exception:
            print("paintGL ERROR:")
            traceback.print_exc()
            self.ok = False

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.RightButton:
            QApplication.quit()
        elif e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            QApplication.quit()


def main() -> int:
    print("python  :", sys.version.split()[0])
    print("PySide6 :", PySide6.__version__)
    print("model   :", MODEL_JSON, "(exists=%s)" % MODEL_JSON.exists())
    print("mode    : canvas=%s app-format=%s legacy-set=%s no-auto=%s" % (
        args.canvas, args.app_format, args.legacy_set, args.no_auto))

    if args.app_format:
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
        fmt.setAlphaBufferSize(8)
        fmt.setSamples(4)
        fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
        QSurfaceFormat.setDefaultFormat(fmt)

    live2d.init()
    app = QApplication(sys.argv[:1])
    win = Win()
    win.show()
    rc = app.exec()
    live2d.dispose()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
