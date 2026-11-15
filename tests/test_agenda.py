"""Feature 17: her own life — a simulated daily agenda (pure, fake clock).

All functions take an explicit ``now`` so tests are deterministic.  The app
wiring is asserted as tripwires (the GUI can't run in the test env).
"""
import random
import sys
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import agenda                                      # noqa: E402


def _t(h, m, weekday=2):
    base = datetime(2026, 10, 28, h, m)          # a Wednesday
    day = datetime(2026, 10, 26, 0, 0) + __import__("datetime").timedelta(days=weekday)
    return day.replace(hour=h, minute=m)


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_hhmm_and_rejects_garbage():
    assert agenda.parse_hhmm("09:30") == 570
    assert agenda.parse_hhmm("00:00") == 0
    assert agenda.parse_hhmm("23:59") == 1439
    for bad in (None, "", "9:30x", "24:00", "09:-5", ("09", "10")):
        assert agenda.parse_hhmm(bad) is None


# ── block selection ──────────────────────────────────────────────────────────

def test_current_block_mid_morning_and_midday():
    b = agenda.current_block(_t(10, 0))           # early calls/showings 08:00-10:30
    assert b is not None and "early showings" in b.label
    b2 = agenda.current_block(_t(11, 0))
    assert b2 is not None and "listing work" in b2.label


def test_block_boundaries_are_start_inclusive_end_exclusive():
    assert agenda.current_block(_t(12, 45)) is None   # 12:45 == end of listing work
    assert agenda.current_block(_t(10, 30)).label.startswith("listing work")


def test_free_time_returns_none():
    assert agenda.current_block(_t(0, 30)) is None
    assert agenda.current_block(_t(19, 0)) is None   # between 18:00 exit and 20:00 sofa
    assert agenda.current_block(_t(23, 50)) is None


def test_lunch_is_a_meal_not_busy():
    b = agenda.current_block(_t(13, 20))
    assert b.kind == "meal"


def test_weekend_uses_weekend_blocks():
    sat = _t(14, 0, weekday=5)
    assert agenda.current_block(sat) is None          # 12:00-13:30 errand ended
    sat_morning = _t(16, 0, weekday=5)
    assert agenda.current_block(sat_morning) is None  # outside the 3 blocks
    b = agenda.current_block(_t(12, 30, weekday=5))
    assert b is not None and "farmers market" in b.label   # weekend errands


def test_disabled_stops_everything():
    cfg = dict(agenda.DEFAULTS)
    cfg["enabled"] = False
    assert agenda.current_block(_t(11, 0), cfg) is None
    assert agenda.prompt_line(_t(11, 0), cfg) == ""
    w = agenda.AgendaWatch(cfg)
    assert w.proactive_line(_t(11, 0), cfg) is None


# ── the prompt line ──────────────────────────────────────────────────────────

def test_prompt_line_shows_inside_block_with_minutes_left():
    line = agenda.prompt_line(_t(11, 0))
    assert "listing work" in line
    assert "min left" in line


def test_prompt_line_free_time_mention():
    line = agenda.prompt_line(_t(19, 0))
    assert "No fixed plan" in line


# ── the proactive excuse watch ──────────────────────────────────────────────

def test_watch_gives_an_excuse_inside_a_busy_block():
    cfg = dict(agenda.DEFAULTS)
    cfg["busy_excuse_chance"] = 1.0
    w = agenda.AgendaWatch(rng=random.Random(7))
    line = w.proactive_line(_t(11, 0, weekday=3), cfg)
    assert line is not None and "listing work" in line


def test_watch_stays_quiet_when_free():
    w = agenda.AgendaWatch(rng=random.Random(7))
    assert w.proactive_line(_t(19, 0)) is None


def test_watch_never_repeats_within_a_block():
    cfg = dict(agenda.DEFAULTS)
    cfg["busy_excuse_chance"] = 1.0
    w = agenda.AgendaWatch(rng=random.Random(7))
    first = w.proactive_line(_t(10, 40), cfg)
    assert first is not None
    assert w.proactive_line(_t(10, 45), cfg) is None      # same block
    assert w.proactive_line(_t(10, 50), cfg) is None


def test_watch_gap_gating_between_blocks():
    cfg = dict(agenda.DEFAULTS)
    cfg["busy_excuse_chance"] = 1.0
    cfg["min_excuse_gap_s"] = 1.0e9                       # basically forever
    w = agenda.AgendaWatch(rng=random.Random(7))
    assert w.proactive_line(_t(10, 40), cfg) is not None
    # a new busy block starts (15:00 edititng) but the gap forbids another
    assert w.proactive_line(_t(15, 10), cfg) is None


def test_watch_chance_zero_is_silent():
    cfg = dict(agenda.DEFAULTS)
    cfg["busy_excuse_chance"] = 0.0
    w = agenda.AgendaWatch(rng=random.Random(7))
    assert w.proactive_line(_t(11, 0), cfg) is None


def test_watch_meal_blocks_are_not_busy():
    cfg = dict(agenda.DEFAULTS)
    cfg["busy_excuse_chance"] = 1.0
    w = agenda.AgendaWatch(rng=random.Random(7))
    assert w.proactive_line(_t(13, 20), cfg) is None      # meal ≠ busy


# ── minute-until transition ─────────────────────────────────────────────────

def test_minutes_until_next_transition():
    # 11:00 on a weekday -> next edge is 12:45 -> 105 minutes
    d = agenda.minutes_until_next_transition(_t(11, 0))
    assert d == 105
    # free hours -> the next block start
    d2 = agenda.minutes_until_next_transition(_t(19, 0))
    assert d2 == 60


# ── config plumbing ─────────────────────────────────────────────────────────

def _write(tmp_path, over=None):
    import yaml
    p = tmp_path / "agenda.yaml"
    p.write_text(yaml.safe_dump(over or {}), encoding="utf-8")
    return p


def test_yaml_roundtrip(tmp_path):
    real = agenda.CONFIG_PATH
    p = _write(tmp_path, {
        "enabled": False,
        "weekday": [{"label": "x", "start": "01:00", "end": "02:00",
                     "kind": "work", "prompt": "p"}],
    })
    agenda.CONFIG_PATH = p
    try:
        cfg = agenda.reload_config()
        assert cfg["enabled"] is False
        assert len(cfg["weekday"]) == 1
        assert cfg["weekday"][0]["kind"] == "work"
    finally:
        agenda.CONFIG_PATH = real
        agenda.reload_config()


def test_live_agenda_is_evelyns_realtor_life():
    """Canon guard: the old writer schedule must never come back."""
    live = agenda.CONFIG_PATH.read_text(encoding="utf-8").lower()
    defaults = repr(agenda.DEFAULTS).lower()
    for blob in (live, defaults):
        assert "writer" not in blob
        assert "chapter" not in blob
        assert "video-essay" not in blob
    assert "showings" in live or "listing" in live
    assert "showings" in defaults or "listing" in defaults


# ── wiring tripwires ─────────────────────────────────────────────────────────

_APP = Path(__file__).parent.parent / "src" / "app.py"


def test_app_uses_the_agenda_prompt_line():
    src = _APP.read_text(encoding="utf-8")
    assert "_agenda_prompt_line" in src


def test_app_picks_agenda_excuses_before_pools():
    src = _APP.read_text(encoding="utf-8")
    assert "_agenda_excuse" in src
    chain = "".join(("self._thread_followup() or self._agenda_excuse()"
                     " or self._choose_proactive()").split())
    assert chain in "".join(src.split())
