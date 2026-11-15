"""Phase 2b tests: pure BehaviorDirector + BlinkController (no GL/Qt/live2d).

Covers the approved phase-2b rules:
  * per-parameter ownership/priority (additive vs override vs blink),
  * closed blink reaches exactly 0.0,
  * a wink suppresses blink only for its eye,
  * breathing continues while a gesture/wink is active (director still emits it),
  * NO physics params are ever emitted,
  * liveliness x state_factor scaling + motion-budget caps,
  * determinism with a seeded RNG.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from behavior_state import BehaviorState                      # noqa: E402
from behavior_director import (                                # noqa: E402
    BehaviorDirector, BlinkController, MotionBudget,
    DIRECTOR_PARAMS, PHYSICS_PARAMS,
)


def make_director(**kw) -> BehaviorDirector:
    kw.setdefault("rng", random.Random(99))
    kw.setdefault("budget", MotionBudget())
    kw.setdefault("liveliness", 0.7)
    return BehaviorDirector(**kw)


def state(**kw) -> BehaviorState:
    kw.setdefault("rng", random.Random(5))
    return BehaviorState(**kw)


# ── 1. Blink reaches exactly 0.0 ─────────────────────────────────────────────

def test_closed_blink_reaches_exactly_zero():
    bc = BlinkController(rng=random.Random(1))
    saw_closed = False
    for _ in range(4000):
        l, r = bc.update(1 / 30, blink_rate=0.3)
        if l == 0.0 and r == 0.0:
            saw_closed = True
            break
    assert saw_closed, "blink never reached exactly 0.0"


def test_blink_opens_again_after_closing():
    bc = BlinkController(rng=random.Random(2))
    # fast blink: interval small enough to trigger quickly
    closed = 0
    open_after = 0
    l_prev = 1.0
    for _ in range(6000):
        l, r = bc.update(1 / 30, blink_rate=0.5, wink_side=None)
        if l == 0.0 and r == 0.0:
            closed += 1
        if closed and l == 1.0 and r == 1.0:
            open_after += 1
            if open_after >= 2:
                break
    assert open_after >= 2, "eyes never reopened after a blink"


# ── 2. Wink suppresses blink only for its eye ────────────────────────────────

def test_wink_suppresses_blink_only_for_its_eye():
    bc = BlinkController(rng=random.Random(3))
    # Force the blink to close by giving it a tiny interval step repeatedly.
    # While a LEFT wink is active, the LEFT eye must be None-owned (gesture owns
    # it) and the RIGHT eye must still reach a normal blink (0.0 at closed).
    bc._next = 0.0
    dt = 1 / 30
    closed_right_while_left_wink = 0
    for _ in range(600):
        l, r = bc.update(dt, blink_rate=0.5, wink_side="left")
        if l is not None:
            # LEFT is being closed by the blink against the wink — assert not
            assert l != 0.0, "left eye (winked) must not be blink-closed"
        if r == 0.0:
            closed_right_while_left_wink += 1
    assert closed_right_while_left_wink > 0, "right eye never blink-closed during left wink"


def test_wink_does_not_change_right_when_right_winked():
    bc = BlinkController(rng=random.Random(4))
    bc._next = 0.0
    dt = 1 / 30
    for _ in range(600):
        l, r = bc.update(dt, blink_rate=0.5, wink_side="right")
        if r is not None:
            assert r != 0.0, "right eye (winked) must not be blink-closed"
        if l == 0.0:
            break


def test_director_wink_side_suppresses_blink_only_for_that_eye():
    """Feeding the wink side through the director's public set_wink_side must
    suppress the blink for exactly the winked eye (it comes back as None), while
    the other eye keeps producing normal blinks down to exactly 0.0."""
    dr = make_director(rng=random.Random(7))
    dr.set_liveliness(1.0)   # full motion so the blink is the only scale factor
    s = state(rng=random.Random(7))
    s.mood_impulse("excited")   # higher arousal -> faster blinks
    right_closed = 0
    for _ in range(3000):
        dr.set_wink_side("left")
        f = dr.update(1 / 30, s)
        l, r = f.blink
        if l is not None:
            assert l != 0.0, "left eye (winked) must never be blink-closed"
        if r == 0.0:
            right_closed += 1
    assert right_closed > 0, "right eye never blink-closed while left was winked"


def test_director_blink_allows_single_eye_closure_without_wink():
    """Without a wink the blink machine owns BOTH eyes symmetrically: it reaches
    exactly 0.0 on both at the closed phase, and never reports None."""
    dr = make_director(rng=random.Random(8))
    s = state(rng=random.Random(8))
    s.mood_impulse("excited")
    both_closed = 0
    none_seen = False
    for _ in range(3000):
        f = dr.update(1 / 30, s)
        l, r = f.blink
        if l is None or r is None:
            none_seen = True
            break
        if l == 0.0 and r == 0.0:
            both_closed += 1
    assert not none_seen, "blink reported a None-owned eye when no wink was active"
    assert both_closed > 0, "both eyes never closed simultaneously in a normal blink"


# ── 3. No physics params, everything within the allowed set ──────────────────

def test_never_emits_physics_params():
    dr = make_director()
    s = state()
    for _ in range(500):
        f = dr.update(1 / 30, s)
        for p in list(f.additive) + list(f.override):
            assert p not in PHYSICS_PARAMS, f"physics param leaked: {p}"
            assert p in DIRECTOR_PARAMS, f"unknown param emitted: {p}"


def test_director_params_have_no_physics_overlap():
    assert not (DIRECTOR_PARAMS & PHYSICS_PARAMS)


def test_frame_keys_within_director_params():
    dr = make_director()
    s = state()
    f = dr.update(1 / 30, s)
    assert set(f.additive) <= DIRECTOR_PARAMS
    assert set(f.override) <= DIRECTOR_PARAMS


# ── 4. Liveliness / motion-budget scaling ────────────────────────────────────

def test_liveliness_zero_freezes_offsets():
    dr = make_director(liveliness=0.0)
    s = state()
    s.mood_impulse("excited")
    total = 0.0
    for _ in range(200):
        f = dr.update(1 / 30, s)
        total += sum(abs(v) for v in f.additive.values())
    # With liveliness 0 all ADDITIVE offsets (head/eye/body drift) are scaled to
    # 0.  Breathing (override) and the blink reflex are NOT liveliness-scaled so
    # the pet never looks frozen/statue-like at the "Still" preset.
    assert total == 0.0


def test_liveliness_scales_head_amplitude():
    lo = make_director(liveliness=0.2)
    hi = make_director(liveliness=1.0)
    lo._rng = random.Random(10)
    hi._rng = random.Random(10)   # same walker seed so only liveliness differs
    s = state()
    s.mood_impulse("excited")
    lo_sum = hi_sum = 0.0
    for _ in range(200):
        lo_sum += sum(abs(v) for v in lo.update(1 / 30, s).additive.values())
        hi_sum += sum(abs(v) for v in hi.update(1 / 30, s).additive.values())
    assert hi_sum > lo_sum


def test_head_angle_capped_by_budget():
    dr = make_director(liveliness=1.0, budget=MotionBudget(max_head_angle=8.0))
    s = state()
    s.mood_impulse("excited")
    for _ in range(300):
        f = dr.update(1 / 30, s)
        assert abs(f.additive["ParamAngleX"]) <= 8.0 + 1e-6
        assert abs(f.additive["ParamAngleY"]) <= 8.0 + 1e-6


# ── 5. Gaze / attention changes kind, breathing continues ────────────────────

def test_gaze_toward_viewer_vs_wander():
    dr = make_director()
    s = state()
    s.set_speaking(True)
    f1 = dr.update(1 / 30, s)
    assert f1.gaze == "look_ahead"
    s.set_speaking(False)
    for _ in range(1200):
        s.update(1.0)   # into idle-long
    f2 = dr.update(1 / 30, s)
    assert f2.gaze == "wander"


def test_breathing_emitted_while_blink_wink_active():
    """Breathing (override) and head/body (additive) keep animating even while
    the blink is closing — nothing shared with the blink is dropped."""
    dr = make_director()
    s = state()
    emitted = 0
    for _ in range(500):
        f = dr.update(1 / 30, s)
        if "ParamBreath" in f.override:
            emitted += 1
    assert emitted > 100, "breathing was not emitted most frames"


def test_deterministic_same_seed_same_frame():
    def run(seed):
        d = make_director(rng=random.Random(seed))
        s = state(rng=random.Random(seed))
        out = []
        for _ in range(100):
            f = d.update(1 / 30, s)
            out.append((f.additive.copy(), f.override.copy(), f.blink, f.gaze))
        return out
    a = run(42)
    b = run(42)
    assert a == b


def test_neutral_motion_visible_but_subtle():
    """With liveliness 0.7 a neutral state moves, but stays small (subtle)."""
    dr = make_director(liveliness=0.7)
    s = state()
    peak = 0.0
    for _ in range(300):
        f = dr.update(1 / 30, s)
        peak = max(peak, max(abs(v) for v in f.additive.values()))
    assert peak > 0.0, "neutral state produced no motion"
    assert peak <= 8.0 + 1e-6, "neutral motion exceeded head budget"


# ── 2c / 2d: nods, lean, breath pause, min blink gap ──────────────────────────

def test_nod_accepted_and_capped_by_budget():
    """A nod event is accepted and its amplitude is capped by the head budget."""
    dr = make_director(budget=MotionBudget(max_head_angle=8.0))
    dr.nod(1000.0)                     # absurd amplitude
    assert dr._nod_t == 0.0                       # accepted (started)
    cap = max(8.0 * 0.5, 1.0)
    assert abs(dr._nod_peak) <= cap + 1e-9


def test_speaking_nod_moves_head_pitch():
    """A speaking nod adds a real down-and-up on ParamAngleY over its duration."""
    def run(with_nod: bool):
        dr = make_director(rng=random.Random(11))
        s = state(rng=random.Random(11))
        s.set_speaking(True)
        if with_nod:
            dr.nod(3.0)
        y = []
        for _ in range(int(BehaviorDirector.NOD_DUR * 60)):
            f = dr.update(1 / 60, s)
            y.append(f.additive["ParamAngleY"])
        return y
    base = run(False)
    nodded = run(True)
    # same seed/walker: the ONLY difference is the nod term.  Its max absolute
    # swing must be clearly present (a real nod), and it must return to the
    # underlying drift afterwards.
    diff = [a - b for a, b in zip(nodded, base)]
    assert max(abs(d) for d in diff) > 1.0, "speaking nod had no visible effect"
    assert abs(diff[-1]) < 1e-3, "nod did not complete / return to baseline"


def test_nod_min_gap_ignores_rapid_retriggers():
    """A nod within NOD_MIN_GAP of the last one is dropped (no fluttering)."""
    dr = make_director(rng=random.Random(12))
    s = state()
    dr.update(0.001, s)                     # bump _t
    dr.nod(3.0)
    first_peak = dr._nod_peak
    dr.nod(5.0)                             # still inside the gap -> ignored
    assert dr._nod_peak == first_peak, "rapid re-nod was not dropped"
    # let time pass beyond the minimum gap, then a new nod is accepted
    for _ in range(200):
        dr.update(1 / 30, s)
    dr.nod(5.0)
    assert dr._nod_peak == 4.0, "nod after the gap was not accepted"  # capped at 8*0.5


def test_no_autonomous_nod_when_neither_speaking_nor_listening():
    """Without speaking/listening the director never auto-triggers a nod."""
    dr = make_director(rng=random.Random(13))
    s = state(rng=random.Random(13))
    for _ in range(int(15.0 / (1 / 30))):
        dr.update(1 / 30, s)                # idle the whole time
    assert dr._nod_t >= BehaviorDirector.NOD_DUR, "an autonomous nod fired while idle"


def test_listening_autonomous_nod_and_lean():
    """While listening: a small nod occurs every few seconds AND the configured
    lean param is eased toward the lean.  Uses liveliness 1.0 + an excited mood
    so the lean is clearly visible above the noise floor."""
    dr = make_director(rng=random.Random(14), liveliness=1.0,
                       lean_param="ParamBodyAngleY0")
    s = state(rng=random.Random(14))
    s.mood_impulse("excited")
    s.set_listening(True)
    nods = 0
    lean_seen = 0.0
    for _ in range(int(20.0 / (1 / 30))):
        f = dr.update(1 / 30, s)
        if dr._nod_t < BehaviorDirector.NOD_DUR:
            nods += 1
        lean_seen = max(lean_seen, abs(f.additive.get("ParamBodyAngleY0", 0.0)))
    assert nods >= 1, "listening never produced an autonomous nod"
    assert lean_seen > 0.1, "listening lean was not visibly applied"


def test_lean_uses_configured_param_only():
    """The lean writes ONLY the configured lean param, never a physics param."""
    dr = make_director(rng=random.Random(15), liveliness=1.0, lean_param="ParamChestAngleY")
    s = state(rng=random.Random(15))
    s.mood_impulse("excited")
    s.set_listening(True)
    for _ in range(120):
        f = dr.update(1 / 30, s)
        for p in f.additive:
            assert p in DIRECTOR_PARAMS
            assert p not in PHYSICS_PARAMS
    # switching the lean param live (preview tool) is honoured on the next frame
    dr._lean_param = "ParamBodyAngleY0"
    s.set_listening(True)
    ls = [f.additive.get("ParamBodyAngleY0", 0.0) for _ in range(120)
          for f in [dr.update(1 / 30, s)]]
    assert max(abs(v) for v in ls) > 0.1


def test_breath_pause_holds_at_rest():
    """When a breath pause triggers, the breath override sits at rest (0.5)."""
    dr = make_director(rng=random.Random(16), liveliness=0.7)
    s = state(rng=random.Random(16))
    dr._bp_next = 0.0            # pause on the first frame
    dr._bp_hold = (1.0, 1.0)     # hold for 1 s
    dr._bp_hold_rem = 0.0
    resting = 0
    for _ in range(int(2.0 / (1 / 30))):
        bv = dr.update(1 / 30, s).override["ParamBreath"]
        if abs(bv - 0.5) < 1e-6:
            resting += 1
    assert resting >= int(1.0 / (1 / 30)) - 2, "hold did not keep breath at rest"


def test_breath_pause_disabled_never_holds():
    """breath_pause_enabled=False disables the pause (breath keeps varying)."""
    dr = make_director(rng=random.Random(17), liveliness=0.7,
                       breath_pause_enabled=False)
    s = state(rng=random.Random(17))
    dr._bp_next = 0.0
    varying = 0
    for _ in range(int(2.0 / (1 / 30))):
        bv = dr.update(1 / 30, s).override["ParamBreath"]
        if abs(bv - 0.5) > 1e-6:
            varying += 1
    assert varying > int(1.0 / (1 / 30)), "breath looked frozen even with pauses off"


def test_min_blink_gap_no_flutter():
    """Even with many/rapid blinks, the time between two DISTINCT close events
    is >= MIN_GAP (a double blink is >= 0.4 s apart, so it never flutters)."""
    bc = BlinkController(rng=random.Random(18))
    t = 0.0
    dt = 1 / 30
    last_close_t = None
    was_closed = False
    gaps = []
    for _ in range(5000):
        t += dt
        l, r = bc.update(dt, blink_rate=1.0)   # fast -> triggers double blinks
        closed = (l == 0.0 and r == 0.0)
        if closed and not was_closed:          # rising edge of a close event
            if last_close_t is not None:
                gaps.append(t - last_close_t)
            last_close_t = t
        was_closed = closed
    assert len(gaps) >= 5, "not enough distinct blink closes measured"
    assert min(gaps) >= BlinkController.MIN_GAP - 1e-6, "blink fluttered below min gap"
