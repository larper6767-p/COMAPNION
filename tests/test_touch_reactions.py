"""Feature 13 Stage B: touch-reaction gating, escalation, mapping, gestures.

All pure: gates and the escalation machine take explicit timestamps; the two
procedural director gestures are driven with seeded RNGs and fake dt.  App
wiring is asserted as source tripwires.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from touch_reactions import (                           # noqa: E402
    gate, resolve, startle_only, get_escalation,
    TouchEscalation, Reaction, EscalationConfig,
    REACTION_SPECS, STARTLE_WHILE_SPEAKING,
    POKE_LEVELS,
    load_spoken, tier, pick_line, VoiceGate,
)
from behavior_director import (                         # noqa: E402
    BehaviorDirector, DIRECTOR_PARAMS,
)
from behavior_state import BehaviorState                # noqa: E402

ESC = EscalationConfig()   # explicit → immune to config-file edits


@pytest.fixture()
def hot_esc(monkeypatch, tmp_path):
    p = tmp_path / "touch_reactions.json"
    monkeypatch.setattr("touch_reactions.DEFAULT_CONFIG_PATH", p)
    import touch_reactions as tr
    tr._cached_cfg = None
    tr._cached_path = None
    yield p
    tr._cached_cfg = None
    tr._cached_path = None


# ── gate ──────────────────────────────────────────────────────────────────────

def test_gate_idle_is_full():
    assert gate("poke", speaking=False, listening=False, dnd=False) == "full"
    assert gate("pat", speaking=False, listening=False, dnd=False) == "full"


def test_gate_dnd_disables_everything():
    assert gate("poke", False, False, dnd=True) == "ignore"
    assert gate("pat", False, False, dnd=True) == "ignore"


def test_gate_recording_ignores_everything():
    assert gate("poke", False, listening=True, dnd=False) == "ignore"
    assert gate("pat", False, listening=True, dnd=False) == "ignore"


def test_gate_speaking_only_allows_small_startle():
    assert gate("poke", speaking=True, listening=False, dnd=False) == "startle_only"
    assert gate("pat", speaking=True, listening=False, dnd=False) == "ignore"
    assert gate("pat_end", speaking=True, listening=False, dnd=False) == "ignore"


# ── poke escalation ───────────────────────────────────────────────────────────

def test_first_poke_is_surprised_then_protest_then_shrug():
    sm = TouchEscalation()
    assert sm.process(0.0, "poke", cfg=ESC).name == "surprised"
    assert sm.process(0.3, "poke", cfg=ESC).name == "surprised"    # count 2.0 < 2.5
    assert sm.process(0.6, "poke", cfg=ESC).name == "playful_protest"   # 3.0 >= 2.5
    sm.process(0.9, "poke", cfg=ESC)                     # 4.0 < 4.5
    assert sm.process(1.2, "poke", cfg=ESC).name == "shrug_off"   # 5.0 >= 4.5


def test_poke_counter_decays_with_half_life():
    sm = TouchEscalation()
    for i in range(5):                                    # count ≈ 5
        sm.process(float(i) * 0.3, "poke", cfg=ESC)
    assert sm.process(0.3 * 4 + 60.0, "poke", cfg=ESC).name == "surprised"
    # 4 s of idle → 0.5 ** (4/15) ≈ 0.83 kept; quick re-poke keeps the level
    sm2 = TouchEscalation()
    sm2.process(0.0, "poke", cfg=ESC)
    sm2.process(1.0, "poke", cfg=ESC)                     # 1 + ~0.95 = 1.95
    assert sm2.process(2.5, "poke", cfg=ESC).name == "playful_protest"


def test_poke_react_spec_shapes():
    assert REACTION_SPECS["surprised"].gesture == "startle"
    assert REACTION_SPECS["playful_protest"].actions == ("gesture:shake",)
    for name in POKE_LEVELS:
        assert name in REACTION_SPECS


# ── pat escalation ────────────────────────────────────────────────────────────

def test_pat_escalates_with_duration():
    sm = TouchEscalation()
    assert sm.process(0.0, "pat", duration=0.8, cfg=ESC).name == "pat_soft_smile"
    sm.process(2.0, "pat_end", duration=1.4, cfg=ESC)     # credit ≈ 2.2
    assert sm.process(3.0, "pat", duration=0.8, cfg=ESC).name == "pat_blush"
    sm.process(5.0, "pat_end", duration=1.0, cfg=ESC)     # credit ≈ 3.3
    r = sm.process(6.0, "pat", duration=0.5, cfg=ESC)     # credit ≥ 3.8
    assert r.name == "pat_stop"


def test_pat_stop_locks_out_touches_then_releases():
    sm = TouchEscalation()
    r = None
    for _ in range(4):                       # credit ~4 s of held pats
        r = sm.process(0.0, "pat", duration=1.0, cfg=ESC)
    assert r is not None and r.name == "pat_stop"
    assert sm.process(2.0, "poke", cfg=ESC) is None       # locked out
    assert sm.process(2.5, "pat", duration=1.0, cfg=ESC) is None
    assert sm.process(ESC.lockout_seconds + 0.1, "poke", cfg=ESC) is not None


def test_sad_state_receives_pats_more_warmly():
    sm = TouchEscalation()
    sad = BehaviorState(rng=random.Random(0))
    sad.valence = ESC.sad_valence - 0.1                   # below the sad line
    sm.process(0.0, "pat", duration=1.0, state=sad, cfg=ESC)   # credit 1.0 ≥ .8*.7
    # sad thresholds ×0.7 → [0.56, 1.4, 2.45]: ~1.58 credit already blushes
    assert sm.process(1.0, "pat", duration=0.6, state=sad, cfg=ESC).name == "pat_blush"
    # the same credit in a NEUTRAL state (thresholds ×1.0 → [0.8, 2, 3.5])
    # is still only the first band
    sm2 = TouchEscalation()
    sm2.process(0.0, "pat", duration=1.0, cfg=ESC)
    assert sm2.process(1.0, "pat", duration=0.6, cfg=ESC).name == "pat_soft_smile"


def test_tired_state_pushes_back_on_pokes_sooner():
    # identical poke cadence: the tired state reaches "shrug off" at the 4th
    # poke (threshold 4.5 × 0.8 = 3.6) while a neutral state is still only
    # playfully protesting (4 pokes ≈ 3.93 < 4.5)
    sm = TouchEscalation()
    tired = BehaviorState(rng=random.Random(0))
    tired.arousal = ESC.tired_arousal - 0.05
    r = None
    for i in range(4):
        r = sm.process(float(i) * 0.3, "poke", state=tired, cfg=ESC)
    assert r.name == "shrug_off"

    sm2 = TouchEscalation()
    r2 = None
    for i in range(4):
        r2 = sm2.process(float(i) * 0.3, "poke", cfg=ESC)
    assert r2.name == "playful_protest"


def test_bad_escalation_section_falls_back(hot_esc):
    hot_esc.write_text('{"escalation": {"poke_levels": [1.0], "lockout_seconds": "x"}}',
                       encoding="utf-8")
    cfg = get_escalation(hot_esc)
    assert cfg == ESC            # broken lists/values keep every default


# ── mapping ───────────────────────────────────────────────────────────────────

def test_resolve_prunes_to_available_actions():
    r = Reaction("t", actions=("gesture:shake", "fx:nope", "gesture:nod"),
                 gesture="content", valence=0.1, arousal=0.0)
    pruned = resolve(r, {"gesture:shake", "gesture:nod", "fx:blush"})
    assert pruned.actions == ("gesture:shake", "gesture:nod")
    assert pruned.gesture == "content"                    # procedural always kept
    assert resolve(r, None).actions == r.actions          # no info → keep all


def test_startle_only_replaces_reaction():
    r = startle_only(REACTION_SPECS["playful_protest"])
    assert r is STARTLE_WHILE_SPEAKING
    assert r.actions == () and r.gesture == "startle"


# ── director touch gestures ───────────────────────────────────────────────────

def _frames(director, state, seconds, step=0.05, start=0.0):
    out = []
    t = start
    while t < start + seconds:
        out.append(director.update(step, state))
        t += step
    return out


def _state(seed=0):
    return BehaviorState(rng=random.Random(seed))


def test_touch_gesture_unknown_name_is_inert():
    d = BehaviorDirector(rng=random.Random(0))
    d.touch_gesture("mystery")
    out = _frames(d, _state(), 0.6)
    # eye openness stays blink-owned; no gesture eye override ever appears
    assert all(f.blink != (None, None) for f in out)
    assert all("ParamEyeLOpen" not in f.additive and
               "ParamEyeROpen" not in f.additive for f in out)


def test_content_gesture_softens_eyes_and_leans_then_ends():
    d = BehaviorDirector(rng=random.Random(0))
    d.touch_gesture("content")
    frames = _frames(d, _state(), 2.4)
    mid = frames[10]                                      # ≈ 0.55 s in: full hold
    assert mid.blink == (None, None)                      # gesture owns the eyes
    assert mid.additive.get("ParamEyeLOpen", 0.0) < -0.2  # softened toward closed
    assert mid.additive.get("ParamEyeROpen", 0.0) < -0.2
    assert "ParamAngleZ" in mid.additive                  # small head lean
    last = frames[-1]
    assert last.blink != (None, None)                     # back to blink-owned
    assert "ParamEyeLOpen" not in last.additive


def test_startle_gesture_quick_widen_and_pullback():
    d = BehaviorDirector(rng=random.Random(0))
    d.touch_gesture("startle")
    frames = _frames(d, _state(), 1.4, step=0.04)
    peak = max(frames, key=lambda f: f.additive.get("ParamEyeLOpen", 0.0))
    assert peak.additive["ParamEyeLOpen"] > 0.5           # quick widen
    assert "ParamAngleY" in peak.additive                 # head pull-back
    assert all(k in DIRECTOR_PARAMS
               for f in frames for k in f.additive)       # structural guarantee


def test_running_gesture_restarts_cleanly():
    d = BehaviorDirector(rng=random.Random(0))
    d.touch_gesture("content")
    _frames(d, _state(), 0.5)                             # mid-envelope
    d.touch_gesture("content")                            # restart
    frames = _frames(d, _state(), 0.4)
    assert frames[-1].additive.get("ParamEyeLOpen", 0.0) < -0.1


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_app_wiring_tripwires():
    root = Path(__file__).parent.parent
    app = (root / "src" / "app.py").read_text(encoding="utf-8", errors="ignore")
    assert "def on_touch_outcome" in app or "_on_touch_outcome(self, oc" in app
    assert "touch_gate(" in app
    assert "touch_startle_only(reaction)" in app
    assert "touch_resolve(" in app
    assert "self.face.play_touch_gesture(reaction.gesture)" in app
    assert "speaker.stop() is never called from here" in app
    face = (root / "src" / "live2d_face.py").read_text(encoding="utf-8",
                                                      errors="ignore")
    assert "def play_touch_gesture" in face


# ── Feature 13 Stage C: spoken reactions ──────────────────────────────────────

def test_spoken_tier_bands():
    assert tier(0) == "cold"
    assert tier(59.9) == "cold"
    assert tier(60) == "warm"
    assert tier(119.9) == "warm"
    assert tier(120) == "close"
    assert tier(172) == "close"
    assert tier("junk") == "cold"


def test_pick_line_uses_the_requested_tier():
    spoken = load_spoken()          # shipped config: all tiers filled
    rng = random.Random(0)
    line = pick_line(spoken, "pat_soft_smile", "close", rng)
    assert line in ("Mm... that's nice. Don't stop.",
                    "Hey, you. That feels good, actually.",
                    "Careful, I could get used to this.",
                    "See? I'm not all cramps and deadlines.")


def test_pick_line_falls_back_to_another_tier(hot_esc):
    hot_esc.write_text(
        '{"spoken": {"lines": {"surprised": {"cold": ["...Yes?"]}}}}',
        encoding="utf-8")
    spoken = load_spoken(hot_esc)
    rng = random.Random(0)
    assert pick_line(spoken, "surprised", "close", rng) == "...Yes?"
    assert pick_line(spoken, "no_such_reaction", "close", rng) is None


def test_test_line_pools_are_pg13_and_clean():
    """Every shipped line: ASCII-safe, not suggestive, no reproach."""
    bad_words = ("sexy", "undress", "punish me", "hate you", "idiot", "stupid")
    for _r, tiers in load_spoken().lines:
        for _t, pool in tiers:
            for line in pool:
                assert line.strip() == line
                low = line.lower()
                assert not any(w in low for w in bad_words), line


def test_voice_gate_min_interval(hot_esc):
    hot_esc.write_text(
        '{"spoken": {"touch_voice": true, "min_interval_s": 10.0, '
        '"hourly_cap": 20}}', encoding="utf-8")
    spoken = load_spoken(hot_esc)
    g = VoiceGate()
    assert g.allow(0.0, spoken) is True
    g.record(0.0, spoken)
    assert g.allow(5.0, spoken) is False
    assert g.allow(10.0, spoken) is True        # exactly at the interval
    g.record(10.0, spoken)
    assert g.allow(15.0, spoken) is False


def test_voice_gate_hourly_cap_and_prune(hot_esc):
    hot_esc.write_text(
        '{"spoken": {"touch_voice": true, "min_interval_s": 0.0, '
        '"hourly_cap": 3}}', encoding="utf-8")
    spoken = load_spoken(hot_esc)
    g = VoiceGate()
    for t in (0.0, 10.0, 20.0):
        assert g.allow(t, spoken) is True
        g.record(t, spoken)
    assert g.allow(30.0, spoken) is False       # cap reached for this hour
    assert g.allow(3600.0 + 1.0, spoken) is True  # window pruned


def test_voice_gate_zero_cap_means_silent(hot_esc):
    hot_esc.write_text('{"spoken": {"hourly_cap": 0}}', encoding="utf-8")
    g = VoiceGate()
    assert g.allow(0.0, load_spoken(hot_esc)) is False


def test_spoken_config_defaults_and_overrides(hot_esc):
    spoken = load_spoken(hot_esc)               # missing file → shipped pool
    assert spoken.enabled is True
    assert spoken.llm_reaction is False
    assert spoken.min_interval_s == 10.0 and spoken.hourly_cap == 6
    hot_esc.write_text('{"spoken": {"touch_voice": false, "hourly_cap": "x", '
                       '"lines": {"junk_reaction": {"close": ["hi"]}}}}',
                       encoding="utf-8")
    spoken2 = load_spoken(hot_esc)
    assert spoken2.enabled is False
    assert spoken2.hourly_cap == 6              # broken value → default
    names = dict(spoken2.lines).keys()
    # user-declared pools are authoritative: the unknown reaction is pruned,
    # so nothing usable remains from this broken set (no shipped resurrection)
    assert "junk_reaction" not in names
    assert names == set()


def test_app_stage_c_tripwires():
    root = Path(__file__).parent.parent
    app = (root / "src" / "app.py").read_text(encoding="utf-8", errors="ignore")
    assert "_maybe_speak_touch" in app
    assert "companion.speaker.say(line, mood=mood)" in app
    assert "self._touch_voice = TouchVoiceGate()" in app
    assert "touch_pick_line(spoken, reaction.name, tier_name, random)" in app
    cfg_text = (root / "config" / "touch_reactions.json").read_text(
        encoding="utf-8")
    import json as _json
    spoken = _json.loads(cfg_text)["spoken"]
    assert spoken["touch_voice"] is True
    assert spoken["touch_llm_reaction"] is False
    assert spoken["min_interval_s"] == 10.0 and spoken["hourly_cap"] == 6
