"""Phase 2a tests: pure BehaviorState model (no GL / Qt / live2d).

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_behavior_state.py -v

Deliberately pure: no QApplication, no clock (dt is passed in), deterministic
via seeded random.Random.  Decay is half-life based and exponential, so results
are independent of step size (see test_step_size_independence).
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from behavior_state import BehaviorState, DEFAULTS  # noqa: E402


def make_state(**kw) -> BehaviorState:
    kw.setdefault("rng", random.Random(1234))
    return BehaviorState(**kw)


# ── 1. Mood impulses move valence/arousal, then decay ────────────────────────

def test_sad_impulse_makes_negative_valence():
    s = make_state()
    s.mood_impulse("sad")
    assert s.valence < 0
    # arousal drops below the 0.5 neutral too
    assert s.arousal < 0.5


def test_joyful_impulse_makes_positive_valence():
    s = make_state()
    s.mood_impulse("joyful")
    assert s.valence > 0
    assert s.arousal > 0.5


def test_unknown_mood_is_noop():
    s = make_state()
    v0, a0 = s.valence, s.arousal
    s.mood_impulse("nonsense")
    assert (s.valence, s.arousal) == (v0, a0)


def test_idle_impulse_is_zero():
    s = make_state()
    v0, a0 = s.valence, s.arousal
    s.mood_impulse("idle")
    assert (s.valence, s.arousal) == (v0, a0)


def test_mood_repeat_diminishes_after_change():
    """Full impulse on a CHANGE, scaled down on a repeat of the same mood."""
    s = make_state()
    s.mood_impulse("joyful")          # change -> full (dvalence 0.6)
    first = s.valence
    assert abs(first - 0.0 - 0.6) < 1e-9
    s.mood_impulse("joyful")          # repeat of joyful -> scaled
    second = s.valence
    assert 0.0 < (second - first) < 0.6 * DEFAULTS["mood_repeat_scale"]
    # a DIFFERENT mood switches back to a full impulse
    s.mood_impulse("sad")
    assert s.valence < second - 0.7   # big drop from a fresh (full) sad impulse


def test_valence_decays_toward_baseline():
    s = make_state()
    s.mood_impulse("sad")
    v0 = s.valence
    # half-life 240 s: after 300 s of updates decay is well under half
    for _ in range(300):
        s.update(1.0)
    assert 0.0 <= abs(s.valence) < abs(v0) * 0.5


def test_arousal_decays_toward_neutral():
    s = make_state()
    s.mood_impulse("excited")
    assert s.arousal > 0.5
    for _ in range(300):            # 300 s vs half-life 90 s -> ~10%
        s.update(1.0)
    assert abs(s.arousal - 0.5) < 0.15


def test_valence_does_not_fully_zero_on_first_frame():
    s = make_state()
    s.mood_impulse("sad")
    v0 = s.valence
    s.update(1 / 30)
    # a single frame only nudges it — decay is gradual, not an instant reset
    assert abs(s.valence) < abs(v0)
    assert abs(s.valence) > abs(v0) * 0.9


def test_sad_state_factor_low_joyful_high():
    sad = make_state()
    sad.mood_impulse("sad")
    sad.update(0.1)
    happy = make_state()
    happy.mood_impulse("joyful")
    happy.mood_impulse("excited")
    happy.update(0.1)
    # a sad / low-arousal state must visibly move slower and less
    assert sad.state_factor() < happy.state_factor()


def test_state_factor_floor_prevents_freezing():
    """Even a fully sad / low-arousal state stays at or above state_floor."""
    s = make_state()
    s.mood_impulse("sad")
    for _ in range(600):
        s.update(1.0)
    floor = DEFAULTS["state_floor"]
    assert s.state_factor() >= floor - 1e-9
    assert s.state_factor() > 0.0


def test_axes_clamped_to_bounds():
    s = make_state()
    for _ in range(200):
        s.mood_impulse("joyful")   # would spike positive
        s.mood_impulse("sad")      # would spike negative
        s.update(0.01)
    assert -1.0 <= s.valence <= 1.0
    assert 0.0 <= s.arousal <= 1.0
    assert 0.0 <= s.engagement <= 1.0


# ── 2. State transitions: listening / speaking / idle / ignored ──────────────

def test_speaking_raises_engagement_toward_attentive():
    s = make_state()
    s.set_speaking(True)
    e0 = s.engagement
    # rises monotonically from neutral
    for _ in range(200):
        s.update(1 / 30)
    assert s.engagement > e0
    # and converges close to the 1.0 attentive target given enough time
    for _ in range(600):
        s.update(1.0)
    assert s.engagement > 0.9


def test_listening_raises_engagement():
    s = make_state()
    s.set_listening(True)
    for _ in range(600):
        s.update(1.0)
    assert s.engagement > 0.9


def test_ignored_then_idle_long_decays_engagement():
    """Engagement drops toward the ignored floor, then the idle-long floor."""
    s = make_state()
    s.engagement = 0.8
    # past ignored_after_s (300 s) and idle_long_after_s (900 s)
    for _ in range(2000):
        s.update(1.0)
    assert s.engagement < 0.4


def test_interact_boosts_engagement_and_resets_timer():
    s = make_state()
    s.engagement = 0.2
    s.update(100.0)  # long idle -> low engagement
    e_before = s.engagement
    s.interact()
    assert s.engagement > e_before
    assert s.seconds_since_interaction == 0.0


# ── 3. Closeness baseline ────────────────────────────────────────────────────

def test_closeness_raises_idle_engagement():
    low = make_state(closeness=0.0)
    high = make_state(closeness=100.0)
    for _ in range(300):
        low.update(1 / 30)
        high.update(1 / 30)
    assert high.engagement > low.engagement


# ── 4. Step-size independence (requirement 6b) ───────────────────────────────

def test_step_size_independence():
    """10 x 0.1 s must equal 1 x 1.0 s for the raw axes and state_factor."""
    def run(step, n):
        s = BehaviorState(rng=random.Random(7), closeness=76.0)
        s.set_speaking(True)              # constant target -> pure exponential
        s.mood_impulse("joyful")
        for _ in range(n):
            s.update(step)
        return s

    a = run(0.1, 10)   # 1.0 s total in 10 steps
    b = run(1.0, 1)    # 1.0 s total in 1 step
    assert abs(a.valence - b.valence) < 1e-9
    assert abs(a.arousal - b.arousal) < 1e-9
    assert abs(a.engagement - b.engagement) < 1e-9
    assert abs(a.state_factor() - b.state_factor()) < 1e-9


# ── 5. Determinism ───────────────────────────────────────────────────────────

def _drive(seed, closeness, events):
    s = BehaviorState(rng=random.Random(seed), closeness=closeness)
    for ev in events:
        if ev == "sad":
            s.mood_impulse("sad")
        elif ev == "joy":
            s.mood_impulse("joyful")
        elif ev == "speak":
            s.set_speaking(True)
        elif ev == "listen":
            s.set_listening(True)
        elif ev == "interact":
            s.interact()
        else:
            s.update(ev)
    return s


def test_deterministic_same_seed_same_result():
    events = [1 / 30] * 50 + ["sad", 1 / 30, "interact", 1 / 30, "listen"] + [1 / 30] * 30
    a = _drive(42, 76.0, events)
    b = _drive(42, 76.0, events)
    assert a.valence == b.valence
    assert a.arousal == b.arousal
    assert a.engagement == b.engagement
    assert a.state_factor() == b.state_factor()
    assert a.blink_rate() == b.blink_rate()
    assert a.sway_amplitude() == b.sway_amplitude()


def test_different_seed_differs_somewhere():
    events = ["joy", "interact"] + [1 / 30] * 40
    a = _drive(1, 0.0, events)
    b = _drive(2, 0.0, events)
    # outputs include seeded noise, so the two runs should not match on the
    # noised outputs (they may match on raw axes, but the derived rates differ).
    assert (a.blink_rate(), a.sway_amplitude(), a.sway_speed(),
            a.breathing_depth(), a.fidget_rate()) != (
        b.blink_rate(), b.sway_amplitude(), b.sway_speed(),
        b.breathing_depth(), b.fidget_rate())


# ── 6. Output ranges / sanity ────────────────────────────────────────────────

def test_outputs_in_valid_ranges():
    s = make_state(closeness=76.0)
    s.mood_impulse("relaxed")
    for _ in range(30):
        s.update(1 / 30)
    assert 0.0 <= s.state_factor() <= 1.0
    assert 0.0 <= s.sway_amplitude() <= 1.0
    assert 0.0 <= s.sway_speed() <= 1.0
    assert 0.0 <= s.breathing_depth() <= 1.0
    assert 0.0 <= s.fidget_rate() <= 1.0
    assert 0.0 <= s.blink_rate() <= 1.0


def test_gaze_spec_modes():
    s = make_state()
    s.set_speaking(True)
    assert s.gaze().mode == "look_ahead" and s.gaze().toward_viewer
    s.set_speaking(False)
    s.set_listening(True)
    assert s.gaze().mode == "attentive" and s.gaze().toward_viewer
    s.set_listening(False)
    # drift into ignored (300s) then idle-long (900s) -> wanders away
    for _ in range(1000):
        s.update(1.0)
    g = s.gaze()
    assert g.mode == "wander" and not g.toward_viewer


def test_zero_dt_noop():
    s = make_state()
    s.update(0.0)
    assert s.valence == 0.0 and s.arousal == 0.5
