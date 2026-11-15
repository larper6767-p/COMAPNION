#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnose whether the Live2D model's procedural motion is actually changing.

Samples widget._current for the key idle params across ~4 s and prints them,
so we can see if breath/sway/pose are oscillating or pinned static.

Run from project root:
    .venv\\Scripts\\python tools\\diagnose_motion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from live2d_face import configure_opengl_format, Live2DWidget
    configure_opengl_format()
except ImportError as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

MODEL_JSON = ROOT / "characters" / "marin" / "live2d" / "简.model3.json"
MOODS_JSON = ROOT / "config" / "live2d_moods.json"

KEYS = ["ParamBreath", "ParamAngleZ", "ParamAngleX", "ParamAngleY",
        "ParamBodyAngleX0", "ParamBodyAngleZ0", "ParamEyeBallX", "ParamEyeBallY"]

samples = []


def sample(tag: str) -> None:
    if widget._model is None or not widget._initialized:
        return
    row = {}
    for k in KEYS:
        row[k] = round(widget._current.get(k, 0.0), 3)
    # capture target + internal state too
    row["\ntgt_AX"] = round(widget._target.get("ParamAngleX", 0.0), 2)
    row["tgt_AY"] = round(widget._target.get("ParamAngleY", 0.0), 2)
    row["tgt_EBX"] = round(widget._target.get("ParamEyeBallX", 0.0), 2)
    row["pose_cur_x"] = round(widget._pose_cur_x, 2)
    row["pose_active"] = widget._pose_active
    row["cur_idle"] = round(widget._cursor_idle_time, 1)
    row["cur_on"] = widget._cursor_tracking
    samples.append((tag, row))


def run() -> None:
    sample("t0")
    QTimer.singleShot(300, lambda: sample("t1"))
    QTimer.singleShot(600, lambda: sample("t2"))
    QTimer.singleShot(900, lambda: sample("t3"))
    QTimer.singleShot(1500, lambda: sample("t4"))
    QTimer.singleShot(2200, lambda: sample("t5"))
    QTimer.singleShot(3000, lambda: sample("t6"))
    QTimer.singleShot(3800, finish)


def finish() -> None:
    sample("t7")
    print("=" * 70)
    print("MOTION DIAGNOSTIC (values written to the model this session)")
    print("=" * 70)
    hdr = "sample".ljust(8)
    for k in KEYS:
        hdr += k.ljust(15)
    print(hdr)
    for tag, row in samples:
        line = tag.ljust(8)
        for k in KEYS:
            line += str(row.get(k, 0.0)).ljust(15)
        print(line)
    print()
    print("Internal state per sample:")
    for tag, row in samples:
        extra = {k: v for k, v in row.items() if k not in KEYS}
        print(tag, extra)
    print()
    print("If ParamBreath / ParamAngleZ vary -> base idle works.")
    print("If tgt_AX/tgt_AY stay near -30 -> cursor tracking is pinning the head.")
    app.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Diagnose Live2D Motion")

    widget = Live2DWidget(MODEL_JSON, MOODS_JSON, parent=None)
    widget.setMinimumSize(300, 300)
    widget.show()

    QTimer.singleShot(2000, run)
    sys.exit(app.exec())
