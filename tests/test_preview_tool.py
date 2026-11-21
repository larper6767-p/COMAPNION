"""Feature 13/15: the Live2D preview & tuning tool (headless smoke test).

The tool is a Qt window around the Live2D widget, which needs a GL context -
so the widget is replaced by a fake here.  What is tested is the part that
actually broke for the user: the window must BUILD, the panel must be
reachable (it is scrollable), the two editors (zones / frame) must not
fight each other, and the save buttons must write the right files.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

from PySide6.QtWidgets import QWidget          # noqa: E402

ROOT = Path(__file__).parent.parent
TOOL = ROOT / "tools" / "live2d_preview.py"


@pytest.fixture(scope="module")
def tool():
    """Import the preview tool as a module (it only runs main() under
    __main__, so importing is safe)."""
    spec = importlib.util.spec_from_file_location("live2d_preview", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live2d_preview"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeWidget(QWidget):
    """Stands in for Live2DWidget (no GL in the test env).  It has to be a
    real QWidget - the canvas puts it into a layout."""

    def __init__(self, *a, **kw):
        super().__init__()
        self._target = {"ParamAngleX": 0.0}
        self._scale = 2.2
        self._offset = (0.0, 0.0)
        self.calls = []

    # geometry / Qt
    def setFixedSize(self, *a):
        self.calls.append(("fixed", a))
        super().setFixedSize(*a)

    def resize(self, *a):
        self.calls.append(("resize", a))
        super().resize(*a)

    def width(self): return super().width() or 450
    def height(self): return super().height() or 450
    def setCursor(self, *a): pass
    def update(self): pass

    # model
    def set_model_scale(self, v): self._scale = v
    def set_model_offset(self, x, y): self._offset = (x, y)
    def get_model_scale(self): return self._scale
    def get_model_offset(self): return self._offset
    def px_per_unit(self): return 10.0
    def canvas_units(self): return (100.0, 200.0)
    def pu_per_scale(self): return 10.0
    def visible_frame(self, w, h=None):
        from core.framing import scale_offset_to_frame
        return scale_offset_to_frame(self._scale, self._offset[0],
                                     self._offset[1], 100.0, 200.0, 10.0,
                                     w, h if h is not None else w)
    def apply_frame(self, frame, w, h=None): self.calls.append(("frame", frame))
    def set_liveliness(self, v): pass
    def set_lean_param(self, name, sign): pass
    def update_art(self, mood, speaking, **kw): pass
    def known_moods(self): return ["idle", "joyful"]
    def apply_body_actions(self, actions): pass
    def play_motion(self, *a, **kw): pass
    def stop_motion(self): pass
    def is_idle_animation_enabled(self): return True
    def set_idle_animation_enabled(self, v): pass
    def set_idle_enabled(self, v): pass
    def set_fps(self, v): pass
    def dispose(self): pass


class _FakeQWidget:
    """Mixin: the fake has to BE a QWidget (it goes into a layout)."""
    pass


@pytest.fixture()
def win(tool, tmp_path, monkeypatch):
    """A built window with the config writes redirected to a temp dir."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    live2d_json = tmp_path / "live2d.json"
    live2d_json.write_text(json.dumps({
        "face_renderer": "live2d",
        "face": {"size": 400, "scale": 1.286, "offset_x": 0.125,
                 "offset_y": -0.173},
    }), encoding="utf-8")
    pet_state = tmp_path / "pet_state.json"
    pet_state.write_text(json.dumps({"face_size": 400}), encoding="utf-8")
    touch_json = tmp_path / "touch_reactions.json"
    touch_json.write_text(json.dumps({
        "enabled": True, "zones_relative": True,
        "zones": {"head": {"x": 0.3, "y": 0.62, "w": 0.4, "h": 0.38},
                  "face": {"x": 0.38, "y": 0.4, "w": 0.24, "h": 0.26}},
    }), encoding="utf-8")

    monkeypatch.setattr(tool, "Live2DWidget", FakeWidget)
    monkeypatch.setattr(tool, "LIVE2D_JSON", live2d_json)
    monkeypatch.setattr(tool, "PET_STATE", pet_state)
    monkeypatch.setattr(tool, "TOUCH_JSON", touch_json)

    w = tool.PreviewWindow()
    yield w, {"live2d": live2d_json, "pet": pet_state, "touch": touch_json}
    w.close()
    app.processEvents()


# ── the window builds and the panel is reachable ─────────────────────────────

def test_window_builds(win):
    w, _ = win
    assert w is not None
    assert w.windowTitle().startswith("Live2D Preview & Tuning")


def test_panel_is_scrollable(win):
    """The panel has seven group boxes - without a scroll area the lower ones
    were clipped off and unreachable."""
    w, _ = win
    from PySide6.QtWidgets import QScrollArea
    scrolls = w.findChildren(QScrollArea)
    assert scrolls, "the right panel must be scrollable"
    # the panel itself is inside one of them
    panel = scrolls[0].widget()
    assert panel is not None
    assert panel.minimumWidth() >= 280


def test_all_groups_are_present(win):
    w, _ = win
    from PySide6.QtWidgets import QGroupBox
    titles = [g.title() for g in w.findChildren(QGroupBox)]
    joined = " | ".join(titles)
    for expected in ("Mood", "Default framing", "Touch zones", "Model frame"):
        assert expected in joined, f"missing group: {expected} ({titles})"


# ── the two editors must not fight each other ───────────────────────────────

def test_zones_and_frame_modes_are_exclusive(win):
    w, _ = win
    w._zones_btn.setChecked(True)
    assert w._zones_mode is True
    w._frame_btn.setChecked(True)
    assert w._frame_mode is True
    assert w._zones_mode is False, "the zone editor must switch off"
    assert w._canvas.frame_mode is True
    assert w._canvas.zones_mode is False


def test_overlay_stays_visible_in_frame_mode(win):
    """Toggling the zones off must not hide the frame editor's overlay."""
    w, _ = win
    w._zones_btn.setChecked(True)
    w._zones_btn.setChecked(False)
    w._frame_btn.setChecked(True)
    assert w._zones_overlay is not None
    # isHidden() is False only when the widget was NOT explicitly hidden
    assert w._zones_overlay.isHidden() is False


def test_frame_mode_disables_the_legacy_sliders(win):
    w, _ = win
    assert w._scale_slider.isEnabled()
    w._frame_btn.setChecked(True)
    assert not w._scale_slider.isEnabled()
    w._frame_btn.setChecked(False)
    assert w._scale_slider.isEnabled()


def test_frame_mode_undoes_the_fixed_widget_size(win):
    w, _ = win
    w._frame_btn.setChecked(True)
    w._frame_btn.setChecked(False)
    assert ("resize", (450, 450)) in w._live2d.calls


# ── the save buttons write the right files ───────────────────────────────────

def test_save_frame_writes_the_frame(win):
    w, files = win
    w._frame_btn.setChecked(True)
    w._save_frame()
    raw = json.loads(files["live2d"].read_text(encoding="utf-8"))
    assert "frame" in raw["face"]
    assert set(raw["face"]["frame"]) == {"x", "y", "w", "h"}


def test_save_pet_size_writes_pet_state(win):
    w, files = win
    w._pet_size_slider.setValue(520)
    w._save_pet_size(520)
    raw = json.loads(files["pet"].read_text(encoding="utf-8"))
    assert raw["face_size"] == 520


def test_save_zones_writes_absolute_model_units(win):
    w, files = win
    w._zones_btn.setChecked(True)
    w._save_zones()
    raw = json.loads(files["touch"].read_text(encoding="utf-8"))
    assert raw["zones_relative"] is False
    assert "head" in raw["zones"]


def test_zone_nudge_uses_a_visible_step(win):
    """A fixed 0.05 model unit is invisible on a canvas of tens of units."""
    w, _ = win
    w._frame_btn.setChecked(True)
    w._zones_btn.setChecked(True)
    name = w._zone_selected
    before = w._zone_state[name][2]
    w._zone_nudge(1.0, 0.0)
    after = w._zone_state[name][2]
    assert after > before
    assert (after - before) > 0.05
