"""Feature 15: the model frame — pure framing maths.

The frame is a rectangle in MODEL space that the face square shows.  These
tests pin down the contract every other part relies on:

* model <-> pixel mapping is exact and round-trips,
* the frame survives a window resize (its model extent does not change),
* frame <-> the SDK's (scale, offset) conversion round-trips,
* zooming keeps the centre, moving keeps the size.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.framing import (                                   # noqa: E402
    Frame, Framing, frame_to_scale_offset, scale_offset_to_frame,
    fit_frame, resize_edge_at,
)


# ── the frame value object ───────────────────────────────────────────────────

def test_frame_centre_and_contains():
    f = Frame(10.0, 20.0, 4.0, 6.0)
    assert f.center_x == 12.0
    assert f.center_y == 23.0
    assert f.contains(11.0, 22.0) is True
    assert f.contains(9.0, 22.0) is False
    assert f.contains(11.0, 27.0) is False


def test_frame_moved_keeps_the_size():
    f = Frame(0.0, 0.0, 4.0, 4.0).moved(1.5, -2.0)
    assert (f.x, f.y, f.w, f.h) == (1.5, -2.0, 4.0, 4.0)


def test_frame_zoomed_keeps_the_centre():
    f = Frame(0.0, 0.0, 10.0, 10.0)
    z = f.zoomed(2.0)
    assert z.w == pytest.approx(5.0)
    assert z.center_x == pytest.approx(f.center_x)
    assert z.center_y == pytest.approx(f.center_y)
    # zooming out works the same way
    assert f.zoomed(0.5).w == pytest.approx(20.0)


def test_frame_fitted_square():
    f = Frame(0.0, 0.0, 10.0, 4.0).fitted_square(6.0)
    assert f.w == 6.0 and f.h == 6.0
    assert f.center_x == pytest.approx(5.0)


def test_frame_from_dict_is_defensive():
    assert Frame.from_dict({"x": 1, "y": 2, "w": 3, "h": 4}) == Frame(1.0, 2.0, 3.0, 4.0)
    assert Frame.from_dict({"x": 1, "y": 2, "w": 0, "h": 4}) is None
    assert Frame.from_dict({"x": 1, "y": 2}) is None
    assert Frame.from_dict("nope") is None
    assert Frame.from_dict({"x": float("nan"), "y": 0, "w": 1, "h": 1}) is None


# ── model <-> pixels ─────────────────────────────────────────────────────────

def test_model_to_px_centres_the_frame():
    f = Frame(0.0, 0.0, 10.0, 10.0)
    fr = Framing(frame=f, width_px=400.0, height_px=400.0)
    # the frame's centre is the canvas's centre
    assert fr.model_to_px(5.0, 5.0) == (pytest.approx(200.0), pytest.approx(200.0))
    # 10 model units across 400 px -> 40 px per unit
    assert fr.px_per_unit == pytest.approx(40.0)
    # +x is right, +y (model up) is UP on screen
    assert fr.model_to_px(6.0, 5.0)[0] == pytest.approx(240.0)
    assert fr.model_to_px(5.0, 6.0)[1] == pytest.approx(160.0)


def test_tall_canvas_maps_both_axes():
    """A tall window (the frame's aspect): the width and the height are
    mapped independently, but the scale stays uniform (no stretching)."""
    f = Frame(0.0, 0.0, 10.0, 15.0)
    fr = Framing(frame=f, width_px=400.0, height_px=600.0)
    assert fr.px_per_unit == pytest.approx(40.0)
    # the frame's centre is the canvas's centre
    assert fr.model_to_px(5.0, 7.5) == (pytest.approx(200.0), pytest.approx(300.0))
    # 5 model units up = 200 px up
    assert fr.model_to_px(5.0, 12.5)[1] == pytest.approx(100.0)


def test_fit_frame_crops_to_the_canvas_aspect():
    """A frame whose aspect does not match the canvas is CROPPED (never
    stretched): the width fills, the height is trimmed around the centre."""
    f = Frame(0.0, 0.0, 10.0, 10.0)
    tall_canvas = fit_frame(f, 400.0, 600.0)
    assert tall_canvas.w == pytest.approx(10.0)
    assert tall_canvas.h == pytest.approx(15.0)      # 10 * 600/400
    assert tall_canvas.center_x == pytest.approx(5.0)
    assert tall_canvas.center_y == pytest.approx(5.0)
    # a matching aspect is left alone
    assert fit_frame(Frame(0, 0, 10, 15), 400, 600) == Frame(0, 0, 10, 15)


def test_px_to_model_round_trips():
    fr = Framing(frame=Frame(-3.0, 7.0, 12.0, 12.0), width_px=333.0,
                      height_px=333.0)
    for px, py in ((0.0, 0.0), (166.5, 166.5), (333.0, 40.0), (12.0, 300.0)):
        mx, my = fr.px_to_model(px, py)
        back = fr.model_to_px(mx, my)
        assert back[0] == pytest.approx(px)
        assert back[1] == pytest.approx(py)


def test_rect_px_gives_left_top_right_bottom():
    fr = Framing(frame=Frame(0.0, 0.0, 10.0, 10.0), width_px=100.0, height_px=100.0)
    l, t, r, b = fr.rect_px((2.0, 2.0, 4.0, 4.0))   # x, y, w, h in model units
    # 10 model units across 100 px -> 10 px per unit, frame centre at (5,5)
    assert l == pytest.approx(20.0)      # x=2  -> 50 + (2-5)*10
    assert r == pytest.approx(60.0)      # x=6
    assert t == pytest.approx(40.0)      # y=6 (top) -> 50 - (6-5)*10
    assert b == pytest.approx(80.0)      # y=2 (bottom)


def test_frame_is_independent_of_the_window_size():
    """The same frame in a bigger square shows the SAME model region - only
    the pixel size changes.  That is the whole point of the frame."""
    frame = Frame(0.0, 0.0, 10.0, 10.0)
    small = Framing(frame=frame, width_px=400.0, height_px=400.0)
    big = Framing(frame=frame, width_px=800.0, height_px=800.0)
    assert small.px_per_unit == pytest.approx(40.0)
    assert big.px_per_unit == pytest.approx(80.0)
    # the model point at the centre is the centre in both
    assert small.model_to_px(5.0, 5.0)[0] == pytest.approx(200.0)
    assert big.model_to_px(5.0, 5.0)[0] == pytest.approx(400.0)


# ── frame <-> the SDK's (scale, offset) ──────────────────────────────────────

def test_frame_to_scale_offset_uses_the_measurement():
    # 400 px square, a 10-unit frame -> 40 px per unit wanted
    # pu_per_scale = 20 px/unit at scale 1 -> scale 2.0
    # the frame is centred on the canvas centre -> no offset needed
    scale, off_x, off_y = frame_to_scale_offset(
        Frame(45.0, 95.0, 10.0, 10.0), canvas_w=100.0, canvas_h=200.0,
        pu_per_scale=20.0, width_px=400.0, height_px=400.0)
    assert scale == pytest.approx(2.0)
    assert off_x == pytest.approx(0.0)
    assert off_y == pytest.approx(0.0)


def test_frame_to_scale_offset_moves_the_frame():
    scale, off_x, off_y = frame_to_scale_offset(
        Frame(60.0, 100.0, 10.0, 10.0), canvas_w=100.0, canvas_h=200.0,
        pu_per_scale=20.0, width_px=400.0, height_px=400.0)
    assert scale == pytest.approx(2.0)
    # the frame centre is 15 units right / 5 units up of the canvas centre,
    # so the model has to be shifted the other way
    assert off_x == pytest.approx(-15.0)
    assert off_y == pytest.approx(-5.0)


def test_frame_scale_offset_round_trip():
    frame = Frame(12.0, 34.0, 8.0, 8.0)
    scale, off_x, off_y = frame_to_scale_offset(
        frame, canvas_w=100.0, canvas_h=200.0, pu_per_scale=17.0,
        width_px=450.0, height_px=450.0)
    back = scale_offset_to_frame(
        scale, off_x, off_y, canvas_w=100.0, canvas_h=200.0,
        pu_per_scale=17.0, width_px=450.0, height_px=450.0)
    assert back.x == pytest.approx(frame.x)
    assert back.y == pytest.approx(frame.y)
    assert back.w == pytest.approx(frame.w)
    assert back.h == pytest.approx(frame.h)


def test_scale_offset_to_frame_uses_the_square_size():
    # at scale 1 with 20 px/unit, a 400 px square shows 20 model units
    f = scale_offset_to_frame(1.0, 0.0, 0.0, canvas_w=100.0, canvas_h=200.0,
                              pu_per_scale=20.0, width_px=400.0, height_px=400.0)
    assert f.w == pytest.approx(20.0)
    assert f.h == pytest.approx(20.0)
    # centred on the canvas centre
    assert f.center_x == pytest.approx(50.0)
    assert f.center_y == pytest.approx(100.0)


def test_degenerate_inputs_do_not_explode():
    assert frame_to_scale_offset(Frame(0, 0, 0, 0), 100, 200, 20, 400, 400) == (1.0, 0.0, 0.0)
    f = scale_offset_to_frame(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 400.0, 400.0)
    assert f.w == 1.0


# ── the free-form resize edge (the window's margin) ──────────────────────────

def test_resize_edge_only_in_the_side_margins():
    size = 400.0
    band = 10.0
    # just outside the left edge of the model square
    assert resize_edge_at(-5.0, 200.0, size, band) == "left"
    assert resize_edge_at(0.0, 200.0, size, band) == "left"
    # just outside the right edge
    assert resize_edge_at(size + 5.0, 200.0, size, band) == "right"
    assert resize_edge_at(size, 200.0, size, band) == "right"
    # inside the model -> not a resize edge (that is a click / poke)
    assert resize_edge_at(200.0, 200.0, size, band) is None
    # beyond the band -> not an edge either
    assert resize_edge_at(-40.0, 200.0, size, band) is None
    assert resize_edge_at(size + 40.0, 200.0, size, band) is None
    # above / below the square (the bubble strips) -> never a resize edge
    assert resize_edge_at(-5.0, -5.0, size, band) is None
    assert resize_edge_at(-5.0, size + 5.0, size, band) is None


def test_resize_edge_handles_degenerate_sizes():
    assert resize_edge_at(-5.0, 10.0, 0.0, 10.0) is None
    assert resize_edge_at(5.0, 5.0, -10.0, 10.0) is None


# ── wiring tripwires ─────────────────────────────────────────────────────────

def _src(name: str) -> str:
    return (Path(__file__).parent.parent / name).read_text(
        encoding="utf-8", errors="ignore")


def test_widget_measures_the_runtime():
    face = _src("src/live2d_face.py")
    assert "def visible_frame" in face
    assert "def apply_frame" in face
    assert "GetCanvasSize" in face          # model units
    assert "GetCanvasSizePixel" in face     # pixels -> the exact px/unit


def test_app_uses_the_frame():
    app = _src("src/app.py")
    assert "def _init_frame" in app
    assert "def _apply_frame" in app
    assert "def _open_frame_editor" in app
    assert "def _reset_frame" in app
    # the continuous resize (2% per notch, no 5% quantisation)
    assert "step * 0.02" in app
    assert "step * 0.05" not in app.split("def wheelEvent")[1][:2000]
    # the pan works on the frame, not on a guessed px-per-unit constant
    assert "self.face.px_per_unit()" in app
    # the frame is persisted in config/live2d.json
    assert 'face["frame"]' in app


def test_window_size_customisation():
    app = _src("src/app.py")
    # free-form resize: the margin next to the model square is a handle
    assert "def _resize_edge_at" in app
    assert "def _resize_to" in app
    assert "self._resize_edge" in app
    # exact numeric entry
    assert "def _ask_face_size" in app
    assert "QInputDialog.getInt" in app
    # the continuous wheel resize
    assert "step * 0.02" in app


def test_window_height_follows_the_frame_aspect():
    """A tall frame = a tall window (her body cropped vertically), and the
    height is DERIVED - never a second stored size."""
    app = _src("src/app.py")
    assert "def _face_height" in app
    assert "frame.h / frame.w" in app
    # the canvas is a rectangle now
    assert "self.face.setGeometry(face_x, ft, s, fh)" in app
    # the height is a property (derived), not an assigned attribute
    assert "self._face_height_px = " not in app


def test_preview_has_the_frame_editor():
    tool = _src("tools/live2d_preview.py")
    assert "def _toggle_frame_mode" in tool
    assert "def _save_frame" in tool
    assert "def _save_pet_size" in tool
    assert "def _reset_frame" in tool
