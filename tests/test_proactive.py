"""Phase 3d tests — state-aware proactive idle messages.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_proactive.py -v

Pure for the chooser/scheduler/tone logic (no Qt, no app).  The one config
test imports core.config (with the external-SDK stubs) to check all the new
Proactive fields load from personality.json.
"""
import importlib
import json
import sys
import types as _types
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _sdk_module(name, **attrs):
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


for _m in ("core", "core.config", "core.model", "core.memory"):
    sys.modules.pop(_m, None)
_sdk_module("cerebras")
_sdk_module("cerebras.cloud")
_sdk_module("cerebras.cloud.sdk", Cerebras=type("Cerebras", (), {}))
_sdk_module("ollama", chat=lambda *a, **k: None, list=lambda *a, **k: [])
_sdk_module("dotenv", load_dotenv=lambda *a, **k: None)

from behavior_state import BehaviorSnapshot  # noqa: E402
from proactive import (  # noqa: E402
    ProactiveChooser,
    ProactiveLimits,
    ProactiveScheduler,
    _DEFAULT_POOLS,
    select_tone,
)


def _snap(valence=0.0, arousal=0.5, engagement=0.5, since=0.0):
    return BehaviorSnapshot(
        valence=valence, arousal=arousal, engagement=engagement,
        seconds_since_interaction=since,
    )


POOLS = {
    "playful": ["p1", "p2", "p3"],
    "gentle": ["g1", "g2"],
    "soft": ["s1"],
}


# ── tone selection (pure, no RNG) ────────────────────────────────────────────

def test_tone_soft_when_sad():
    assert select_tone(_snap(valence=-0.5)) == "soft"


def test_tone_gentle_when_ignored_long():
    # ignored beyond idle_long_after (900 s) -> gentle, when not sad
    assert select_tone(_snap(valence=0.5, since=1000.0)) == "gentle"
    # sad takes priority: "soft when sad" (and she still speaks LESS because
    # the chooser suppresses ignored-long)
    assert select_tone(_snap(valence=-0.5, since=1000.0)) == "soft"


def test_tone_playful_default():
    assert select_tone(_snap(valence=0.5, since=30.0)) == "playful"
    assert select_tone(None) == "playful"            # no snapshot


def test_tone_playful_below_idle_long_threshold():
    # ignored but not yet "idle long" -> still playful
    assert select_tone(_snap(valence=0.5, since=500.0)) == "playful"


# ── chooser pools + suppression ─────────────────────────────────────────────

def test_chooser_picks_from_tone_pool():
    c = ProactiveChooser(pools=POOLS, rng=__import__("random").Random(0))
    assert c.choose(_snap(valence=-0.5)) in POOLS["soft"]
    assert c.choose(_snap(valence=0.5, since=30)) in POOLS["playful"]
    # gentle tone is resolved to the gentle pool (suppression is separate and
    # may return None, which is covered by the ignored-long tests)
    assert c.tone(_snap(valence=0.5, since=1000)) == "gentle"
    assert c._resolve_pool("gentle") == POOLS["gentle"]


def test_chooser_deterministic_with_seed():
    rng1, rng2 = __import__("random").Random(7), __import__("random").Random(7)
    a = ProactiveChooser(pools=POOLS, rng=rng1)
    b = ProactiveChooser(pools=POOLS, rng=rng2)
    snaps = [_snap(valence=0.5, since=10), _snap(valence=-0.2, since=20)]
    a_out = [a.choose(s) for s in snaps]
    b_out = [b.choose(s) for s in snaps]
    assert a_out == b_out


def test_ignored_long_suppresses_often():
    """Ignored long -> mostly None (FEWER messages, never more insistent)."""
    import random
    c = ProactiveChooser(pools=POOLS, rng=random.Random(1))
    fires = sum(1 for _ in range(200)
                if c.choose(_snap(valence=0.5, since=500.0)) is not None)
    # between ignored_after (300) and idle_long_after (900): 40%..~51% suppress
    assert fires < 130
    assert fires > 0


def test_ignored_extremely_long_heavily_suppressed():
    import random
    c = ProactiveChooser(pools=POOLS, rng=random.Random(2))
    fires = sum(1 for _ in range(200)
                if c.choose(_snap(valence=0.5, since=5000.0)) is not None)
    assert fires < 60                 # capped at ~15% emit


def test_chooser_not_ignored_never_suppresses():
    import random
    c = ProactiveChooser(pools=POOLS, rng=random.Random(3))
    for _ in range(200):
        assert c.choose(_snap(valence=0.5, since=0.0)) is not None


def test_chooser_empty_snapshot_falls_back_to_playful():
    c = ProactiveChooser(pools=POOLS, rng=__import__("random").Random(4))
    assert c.choose(None) in POOLS["playful"]


def test_no_reproach_pools():
    """Approved rule: no reproach / 'you finally noticed me'.  Content is in the
    pools, so scan every default + provided pool for forbidden phrasing."""
    forbidden = ("ignored!", "noticed me", "forget about me", "forgotten about me",
                 "so rude", "will not be ignored", "talking to myself")
    for pool in list(_DEFAULT_POOLS.values()) + list(POOLS.values()):
        for msg in pool:
            low = msg.lower()
            for word in forbidden:
                assert word not in low, (word, msg)


# ── scheduler limits (pure, explicit now) ────────────────────────────────────

def test_scheduler_min_interval_enforced():
    lim = ProactiveLimits(min_interval_minutes=30, daily_cap=100,
                          quiet_start_hour=0, quiet_end_hour=0)  # no quiet hours
    s = ProactiveScheduler(lim)
    t0 = datetime(2026, 10, 11, 12, 0, 0)
    assert s.allowed(t0) is True
    s.record(t0)
    # 1 minute later -> not allowed (min gap 30 min)
    assert s.allowed(t0 + timedelta(minutes=1)) is False
    # 31 minutes later -> allowed again
    assert s.allowed(t0 + timedelta(minutes=31)) is True


def test_scheduler_daily_cap_resets_next_day():
    lim = ProactiveLimits(min_interval_minutes=0, daily_cap=2,
                          quiet_start_hour=0, quiet_end_hour=0)
    s = ProactiveScheduler(lim)
    day = datetime(2026, 10, 11, 12, 0, 0)
    assert s.allowed(day) is True
    s.record(day)
    assert s.allowed(day) is True
    s.record(day)
    # capped out today
    assert s.allowed(day) is False
    # next day resets
    assert s.allowed(day + timedelta(days=1)) is True


def test_scheduler_quiet_hours_wrap_midnight():
    lim = ProactiveLimits(min_interval_minutes=0, daily_cap=100,
                          quiet_start_hour=23, quiet_end_hour=7)
    s = ProactiveScheduler(lim)
    assert s.allowed(datetime(2026, 10, 11, 12, 0, 0)) is True    # afternoon
    assert s.allowed(datetime(2026, 10, 11, 23, 30, 0)) is False  # night
    assert s.allowed(datetime(2026, 10, 12, 2, 0, 0)) is False    # past midnight
    assert s.allowed(datetime(2026, 10, 12, 8, 0, 0)) is True     # morning after quiet


def test_scheduler_quiet_hours_disabled_when_equal():
    lim = ProactiveLimits(min_interval_minutes=0, daily_cap=100,
                          quiet_start_hour=7, quiet_end_hour=7)
    s = ProactiveScheduler(lim)
    assert s.allowed(datetime(2026, 10, 11, 7, 0, 0)) is True


def test_scheduler_dnd_blocks_everything():
    lim = ProactiveLimits(min_interval_minutes=0, daily_cap=100,
                          quiet_start_hour=0, quiet_end_hour=0, dnd=True)
    s = ProactiveScheduler(lim)
    assert s.allowed(datetime(2026, 10, 11, 12, 0, 0)) is False


def test_scheduler_ignored_long_widens_gap():
    lim = ProactiveLimits(min_interval_minutes=30, daily_cap=100,
                          quiet_start_hour=0, quiet_end_hour=0,
                          ignored_min_interval_mult=1.5)
    s = ProactiveScheduler(lim)
    t0 = datetime(2026, 10, 11, 12, 0, 0)
    assert s.allowed(t0) is True
    s.record(t0)
    # normal gap 30 min: at 31 min allowed when not ignored
    assert s.allowed(t0 + timedelta(minutes=31), ignored_long=False) is True
    # but with ignored_long the gap is 45 min -> still blocked at 31 min
    assert s.allowed(t0 + timedelta(minutes=31), ignored_long=True) is False
    assert s.allowed(t0 + timedelta(minutes=46), ignored_long=True) is True


# ── config integration (loads new fields from personality.json) ─────────────

def test_config_loads_proactive_fields():
    core_config = importlib.import_module("core.config")
    Config = core_config.Config
    cfg = Config()   # loads the real project config/ (incl. new proactive fields)
    assert isinstance(cfg.proactive_pools, dict)
    assert set(cfg.proactive_pools) >= {"playful", "gentle", "soft"}

    # These knobs are USER-TUNED in personality.json (numbers/bools only —
    # persona text is never touched).  Compare the loader against the file
    # itself instead of pinning old constants, so tuning the config can
    # never turn this into a false failure.
    persona_path = Path(__file__).parent.parent / "config" / "personality.json"
    raw = json.loads(persona_path.read_text(encoding="utf-8"))
    assert cfg.proactive_min_interval_minutes == float(
        raw["proactive_min_interval_minutes"])
    assert cfg.proactive_daily_cap == int(raw["proactive_daily_cap"])
    assert cfg.proactive_quiet_start_hour == int(
        raw["proactive_quiet_start_hour"])
    assert cfg.proactive_quiet_end_hour == int(
        raw["proactive_quiet_end_hour"])
    assert cfg.proactive_dnd is bool(raw["proactive_dnd"])
    assert cfg.proactive_generate is bool(raw["proactive_generate"])
    # structural invariants that hold whatever the user tunes
    assert cfg.proactive_min_interval_minutes > 0
    assert cfg.proactive_daily_cap >= 1
    assert 0 <= cfg.proactive_quiet_start_hour <= 23
    assert 0 <= cfg.proactive_quiet_end_hour <= 23
    # the LLM-offered mood list still excludes idle (3a invariant)
    assert "idle" not in cfg.persona.get("moods", [])
