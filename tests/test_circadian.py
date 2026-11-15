"""Feature 16: time-awareness — her circadian rhythm (pure, fake clock).

Nothing here reads the wall clock unless explicitly asked to: every function
takes an explicit ``now`` (or pinned hour/weekday), so the tests are
deterministic.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── the pure module (real, not stubbed) ──────────────────────────────────────

from core import circadian                                    # noqa: E402


@pytest.fixture()
def hot_cfg(tmp_path):
    """A private config file, reloaded around each test."""
    p = tmp_path / "circadian.yaml"
    yield p
    circadian.reload_config()


# ── tier selection patterns ─────────────────────────────────────────────────

def test_late_night_includes_both_sides_of_midnight():
    # start 23, end 5 -> wrap: 23:00..04:59 are late night
    for h in (23, 0, 3, 4):
        e = circadian.snapshot(hour=h, weekday=2)   # a Wednesday
        assert e is not None and "late night" in e.label


def test_dawn_starts_at_05_exactly():
    e23 = circadian.snapshot(hour=4, weekday=2)
    e05 = circadian.snapshot(hour=5, weekday=2)
    assert e23.label == "late night"
    assert e05.label == "dawn"
    assert e05.dvalence > 0.0


def test_daytime_and_evening_boundary():
    assert circadian.snapshot(hour=16, weekday=4).label == "daytime"
    assert circadian.snapshot(hour=17, weekday=4).label == "evening"


def test_weekend_overrides_and_no_weekend_falls_back(tmp_path):
    # Saturday (weekday=5) at 14:00 -> the WEEKEND afternoon tier
    e = circadian.snapshot(hour=14, weekday=5)
    assert e is not None and e.label == "weekend afternoon"
    # Wednesday at 14:00 -> plain daytime
    assert circadian.snapshot(hour=14, weekday=3).label == "daytime"
    # empty weekend list reuses the weekday tiers
    cfg = dict(circadian.DEFAULTS)
    cfg["weekend"] = []
    e2 = circadian.snapshot(hour=14, weekday=5, cfg=cfg)
    assert e2 is not None and e2.label == "daytime"


def test_disabled_returns_none_and_empty_line(tmp_path):
    cfg = dict(circadian.DEFAULTS)
    cfg["enabled"] = False
    now = datetime(2026, 10, 28, 14, 30)
    assert circadian.snapshot(now, cfg) is None
    assert circadian.prompt_line(now, cfg) == ""
    assert circadian.bias(circadian.snapshot(now, cfg)) == (0.0, 0.0, 0.0)


def test_bias_passes_through_and_defaults_zero():
    e = circadian.snapshot(hour=2, weekday=2)     # deep night
    dv, da, de = circadian.bias(e)
    assert dv < 0 and da < 0 and de < 0
    assert circadian.bias(None) == (0.0, 0.0, 0.0)


def test_prompt_line_carries_clock_and_label():
    now = datetime(2026, 10, 28, 23, 45)          # a Wednesday
    line = circadian.prompt_line(now)
    assert "23:45" in line and "Wednesday" in line
    assert "late night" in line
    assert "wear" in line.lower() or "worn" in line


def test_describe_time_shape():
    now = datetime(2026, 10, 31, 14, 5)           # a Saturday
    s = circadian.describe_time(now)
    assert s.startswith("It's 14:05 on a Saturday")
    assert "weekend afternoon" in s
    # disabled -> still a plain, usable hint
    cfg = dict(circadian.DEFAULTS)
    cfg["enabled"] = False
    assert circadian.describe_time(now, cfg).startswith("It's 14:05")


# ── BehaviorState integration ────────────────────────────────────────────────

def test_state_bias_pulls_the_arousal_target():
    from behavior_state import BehaviorState
    st = BehaviorState(closeness=0)
    st.arousal = 0.5
    st.set_circadian_bias(0.0, -0.5, 0.0)          # the small-hours drowsiness
    st.update(dt=1800.0)                           # 30 min >> the 90 s half-life
    assert st.arousal < 0.05


def test_state_zero_bias_is_today_behaviour():
    from behavior_state import BehaviorState
    st = BehaviorState(closeness=0)
    st.arousal = 0.5
    st.set_circadian_bias(0.0, 0.0, 0.0)
    st.update(dt=1800.0)
    assert st.arousal == pytest.approx(0.5, abs=0.01)


def test_state_bias_clamped_and_reversible():
    from behavior_state import BehaviorState
    st = BehaviorState(closeness=0)
    st.set_circadian_bias(9.9, -9.9, 9.9)          # request absurd values
    assert st._circadian == (1.0, -1.0, 1.0)
    st.set_circadian_bias(0.0, 0.0, 0.0)
    assert st._circadian == (0.0, 0.0, 0.0)
    st.update(dt=1800.0)
    assert st.arousal == pytest.approx(0.5, abs=0.01)


# ── config plumbing ─────────────────────────────────────────────────────────

def test_config_loads_and_keeps_defaults():
    cfg = circadian.get_config()
    assert cfg["enabled"] is True
    assert cfg["weekday"], "weekday tiers must exist by default"
    assert any(t["start"] > t["end"] for t in cfg["weekday"]), \
        "the 23->5 wrap tier must be present by default"


def test_yaml_roundtrip_wins_over_defaults(tmp_path):
    import yaml
    real = circadian.CONFIG_PATH
    p = tmp_path / "circadian.yaml"
    p.write_text(yaml.safe_dump(
        {"enabled": False, "weekday": [{"label": "never", "start": 1, "end": 2,
                                        "dvalence": 0.4}]},
    ), encoding="utf-8")
    circadian.CONFIG_PATH = p
    try:
        cfg = circadian.reload_config()
        assert cfg["enabled"] is False
        assert len(cfg["weekday"]) == 1
        assert cfg["weekday"][0]["dvalence"] == 0.4
    finally:
        circadian.CONFIG_PATH = real
        circadian.reload_config()


# ── wiring tripwires (app must actually call this) ──────────────────────────

_APP = Path(__file__).parent.parent / "src" / "app.py"


def test_app_calls_the_circadian_tick():
    src = _APP.read_text(encoding="utf-8")
    assert "circadian_tick" in src, "the pet tick must call circadian_tick"
    assert "set_circadian_bias" in src or "circadian" in src


def test_app_injects_the_time_line_into_the_prompt():
    src = _APP.read_text(encoding="utf-8")
    assert "_circadian_prompt_line" in src, \
        "system_message must append the circadian line"


def test_behavior_state_has_the_bias_api():
    src = (Path(__file__).parent.parent / "src" / "behavior_state.py") \
        .read_text(encoding="utf-8")
    assert "def set_circadian_bias" in src
