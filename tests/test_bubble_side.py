"""The speech bubble opens on the LEFT of her face; SHE never moves.

Offscreen (no GL): drives the real bubble path (`on_start`/`on_text` → the
slot state machine → `_apply_layout`/`_place_bubble`) and measures actual
widget geometry — `pet.face.x()` and `mapToGlobal`, NOT the nominal
`_face_x_px`.  (A bug shipped once where the nominal offset and the real
widget disagreed by a whole panel width: the bubble was drawn ON her and
she slid sideways every time the speech bubble appeared.)

User rules this file locks in:
  * the face widget sits exactly where the placement math believes she is
  * opening (and closing) the panel never moves her on screen
  * the panel is on her left, hugging her, narrower than the face, and may
    clip off-screen at the edge instead of shoving her out of her spot
  * the tail shows and its point touches her face edge
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import test_headless as TH                    # performs stubbing + app import
APP = TH.APP

from PySide6.QtCore import QPoint             # noqa: E402

_TEXT = ("Hello there, this is a fairly long speech bubble line so the "
         "manga side panel is definitely open for the probe.")


def _spans(pet):
    """Return (face_x, face_r, bubble_x, bubble_r) from REAL widget geometry."""
    qa = TH._app
    qa.processEvents()
    fx = pet.face.x()
    fr = fx + pet.face.width()
    bx = pet.bubble.x()
    br = bx + pet.bubble.width()
    return fx, fr, bx, br


def _screen_face_pos(pet):
    TH._app.processEvents()
    return pet.face.mapToGlobal(QPoint(0, 0))


def _open_bubble(pet):
    pet.show()
    pet.on_start()
    pet.on_text(_TEXT)


def test_face_widget_matches_nominal_offset():
    """The actual face widget must sit at `_face_x_px` — the placement math
    and the reality must never drift apart."""
    pet = APP.Pet(on_click=lambda: None)
    _open_bubble(pet)
    _spans(pet)
    assert pet.face.x() == pet._face_x_px, (
        f"face widget at {pet.face.x()} but math believes {pet._face_x_px} "
        f"— placement would draw the bubble at her")
    pet.close()


def test_face_stays_pinned_when_panel_opens():
    """Widening the window for the side column must grow the window AWAY
    from her — her face's SCREEN position must not move at all."""
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    before = _screen_face_pos(pet)
    _open_bubble(pet)
    after = _screen_face_pos(pet)
    assert (before.x(), before.y()) == (after.x(), after.y()), (
        f"face moved on screen when the panel opened: "
        f"{(before.x(), before.y())} -> {(after.x(), after.y())}")
    pet.close()


def test_face_stays_pinned_when_panel_closes():
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    at_rest = _screen_face_pos(pet)
    _open_bubble(pet)
    _spans(pet)
    pet.on_end()          # turn finished → panel closes, window shrinks back
    back = _screen_face_pos(pet)
    assert (at_rest.x(), at_rest.y()) == (back.x(), back.y()), (
        f"face did not return to its park position: "
        f"{(at_rest.x(), at_rest.y())} -> {(back.x(), back.y())}")
    pet.close()


def test_speech_bubble_opens_on_her_left():
    pet = APP.Pet(on_click=lambda: None)
    _open_bubble(pet)
    fx, fr, bx, br = _spans(pet)
    assert getattr(pet, "_bubble_shown", None) is True, \
        "a spoken bubble must open the side column"
    assert getattr(pet, "_bubble_side", None) == -1, \
        "the side column must be on her LEFT"
    assert br <= fx, (
        f"bubble [{bx}..{br}] should sit left of her face [{fx}..{fr}]")
    pet.close()


def test_panel_hugs_the_face():
    """User request: the panel sits close to her — exactly `gap` px."""
    pet = APP.Pet(on_click=lambda: None)
    _open_bubble(pet)
    fx, _fr, _bx, br = _spans(pet)
    assert fx - br == pet._bubble_gap, (
        f"gap between panel right edge ({br}) and her face ({fx}) is "
        f"{fx - br}, expected {pet._bubble_gap}")
    pet.close()


def test_panel_is_narrow_when_there_is_room():
    """User request: the column takes up less window space than the old
    0.9*face monster — ~0.55*face, floored at 200 px.  The width is reserved
    permanently (not computed from the current screen room) so revealing the
    panel never resizes the window."""
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    pet.move(600, 400)          # roomy spot far from the screens' left edge
    _open_bubble(pet)
    _spans(pet)
    wanted = max(200, int(pet.face_size * 0.55))
    assert pet._bubble_bw == wanted, (
        f"panel width {pet._bubble_bw}, expected {wanted}")
    pet.close()


def test_tail_point_touches_her_face():
    pet = APP.Pet(on_click=lambda: None)
    _open_bubble(pet)
    fx, _fr, _bx, _br = _spans(pet)
    tail = pet._bubble_tail
    assert tail.isVisible(), "the tail must show in side mode"
    assert abs((tail.x() + tail.width()) - fx) <= 1, (
        f"tail sharp point at {tail.x() + tail.width()}, face edge at {fx}")
    pet.close()


def test_reveal_never_resizes_or_moves_the_window():
    """Regression (user report: the model flickered on hover/speech).

    Revealing the panel used to resize the translucent window, move it, and
    only then reposition the OpenGL face — a one-frame sideways jump.  The
    column is now permanently reserved: the window geometry must be identical
    at rest and with the panel open, and only the window mask changes.
    """
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    TH._app.processEvents()
    geo = (pet.x(), pet.y(), pet.width(), pet.height())
    hidden = pet.mask()
    assert not hidden.isEmpty(), \
        "resting window must be masked to the compact frame"
    assert hidden.contains(pet.face.geometry().center()), \
        "the face must be inside the resting mask"
    assert not hidden.contains(QPoint(2, 2)), \
        "the reserved panel column must be click-through while hidden"

    _open_bubble(pet)
    _spans(pet)
    assert (pet.x(), pet.y(), pet.width(), pet.height()) == geo, \
        "revealing the panel must not resize or move the window"
    shown = pet.mask()
    assert shown.contains(pet.bubble.geometry().center()), \
        "the visible bubble must be inside the window mask"
    assert shown.contains(pet.face.geometry().center())

    pet._apply_slot("input")      # panel closes, typing box takes the slot
    assert (pet.x(), pet.y(), pet.width(), pet.height()) == geo, \
        "hiding the panel must not resize or move the window"
    back = pet.mask()
    assert not back.contains(QPoint(2, 2)), \
        "the column must become click-through again once the panel is hidden"
    assert back.contains(pet.face.geometry().center())
    if pet.input_bubble is not None and pet.input_bubble.isVisible():
        assert back.contains(pet.input_bubble.geometry().center()), \
            "the inline input must stay inside the resting mask"
    pet.close()


def test_left_edge_she_stays_put_panel_may_clip():
    """Parked at the screen's left edge the full-width panel may run off-
    screen, but she must never be shoved right — and (unlike the old
    shrink-to-fit behaviour) the window must not move or resize either."""
    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    # Park her FACE at the screen's left edge: the real window includes the
    # permanently reserved panel column, so shift the window left by it.
    pet.move(-pet._compact_left(), 400)
    parked = _screen_face_pos(pet)
    geo = (pet.x(), pet.y(), pet.width(), pet.height())
    _open_bubble(pet)
    fx, fr, bx, br = _spans(pet)
    after = _screen_face_pos(pet)
    assert (pet.x(), pet.y(), pet.width(), pet.height()) == geo, \
        "opening the panel must not move or resize the window"
    assert (parked.x(), parked.y()) == (after.x(), after.y()), \
        "the clamp must never drag her out of her spot"
    wanted = max(200, int(pet.face_size * 0.55))
    assert pet._bubble_bw == wanted, (
        f"panel width {pet._bubble_bw}, expected the reserved {wanted}px")
    assert br <= fx, f"bubble [{bx}..{br}] went right of her face [{fx}..{fr}]"
    bubble_global_x = pet.bubble.mapToGlobal(QPoint(0, 0)).x()
    assert bubble_global_x < 0, (
        "at the left screen edge the full-width panel is allowed to clip "
        f"off-screen (bubble starts at screen x={bubble_global_x})")
    pet.close()


def test_parked_position_roundtrips_with_the_reserved_column(
        tmp_path, monkeypatch):
    """Save/restore still speaks in COMPACT-frame coordinates: a restart must
    put her face back on the exact same screen spot even though the real
    window now extends left by the reserved panel column."""
    state = tmp_path / "pet_state.json"
    monkeypatch.setattr(APP, "STATE_FILE", state)

    pet = APP.Pet(on_click=lambda: None)
    pet.show()
    compact_x, compact_y = 600, 350
    pet.move(compact_x - pet._compact_left(), compact_y)
    pet.save_position()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert (saved["x"], saved["y"]) == (compact_x, compact_y)

    pet2 = APP.Pet(on_click=lambda: None)
    pet2.restore_position(APP.QGuiApplication.primaryScreen())
    pet2.show()
    TH._app.processEvents()
    assert pet2.x() + pet2._compact_left() == compact_x, \
        "restore must offset the saved compact frame past the reserved column"
    assert pet2.y() == compact_y
    assert _screen_face_pos(pet2) == _screen_face_pos(pet)
    pet.close()
    pet2.close()
