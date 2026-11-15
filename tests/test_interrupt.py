"""Feature 19: interruption manners — recovery gate, config, wiring tripwires.

The core gate is pure (explicit ``now``); the audio/streaming behaviour
itself can't run headless, so the app-side integration is asserted as
static tripwires.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import interruption                                # noqa: E402


NOW = datetime(2026, 11, 5, 15, 0)


def _cfg(**over):
    cfg = dict(interruption.DEFAULTS)
    cfg.update(over)
    return cfg


# ── the recovery gate ────────────────────────────────────────────────────────

def test_yield_allowed_when_mid_reply():
    gate = interruption.RecoveryGate()
    line = gate.allow(NOW, spoken_chars=120)
    assert line is not None and line != ""


def test_silent_stop_for_a_mere_tap():
    gate = interruption.RecoveryGate()
    assert gate.allow(NOW, spoken_chars=3) is None


def test_disabled_means_no_yield():
    gate = interruption.RecoveryGate()
    assert gate.allow(NOW, spoken_chars=500, cfg=_cfg(enabled=False)) is None


def test_gap_gating_between_yields():
    gate = interruption.RecoveryGate()
    first = gate.allow(NOW, spoken_chars=120)
    assert first is not None
    assert gate.allow(NOW + timedelta(seconds=5), spoken_chars=80) is None
    later = gate.allow(NOW + timedelta(minutes=5), spoken_chars=80)
    assert later is not None           # the same string may be redrawn by
                                       # chance; what matters is THAT she
                                       # yields again after the gap


def test_max_streak_then_stale_reset():
    gate = interruption.RecoveryGate()
    cfg = _cfg(recovery_min_gap_s=1.0)
    t = NOW
    assert gate.allow(t, 200, cfg) is not None
    t += timedelta(seconds=30)                 # beyond the min gap
    assert gate.allow(t, 200, cfg) is not None # streak 2 == max
    t += timedelta(seconds=30)
    assert gate.allow(t, 200, cfg) is None     # streak over the limit
    t += timedelta(seconds=700)                # past recovery_stale_s
    assert gate.allow(t, 200, cfg) is not None  # slate wiped again


def test_drop_in_pool_returns_none_and_records_nothing():
    gate = interruption.RecoveryGate()
    assert gate.allow(NOW, spoken_chars=200, cfg=_cfg(recovery_lines=[])) \
        is None


def test_streak_counts_only_real_yields():
    gate = interruption.RecoveryGate()
    assert gate.allow(NOW, spoken_chars=2) is None          # too short: no
    assert gate.allow(NOW, spoken_chars=300) is not None    # but streak is 0->1
    assert gate.streak == 1


def test_line_has_no_placeholders():
    gate = interruption.RecoveryGate()
    line = gate.allow(NOW, spoken_chars=300)
    assert "{" not in line and "}" not in line


# ── config plumbing ─────────────────────────────────────────────────────────

def test_config_roundtrip(tmp_path):
    import yaml
    p = tmp_path / "interruption.yaml"
    p.write_text(yaml.safe_dump({
        "enabled": False,
        "recovery_lines": ["go on."],
        "hotkey": "ctrl+alt+q",
        "recovery_min_gap_s": 45.0,
    }), encoding="utf-8")
    real = interruption.CONFIG_PATH
    interruption.CONFIG_PATH = p
    try:
        cfg = interruption.reload_config()
        assert cfg["enabled"] is False
        assert cfg["recovery_lines"] == ["go on."]
        assert cfg["hotkey"] == "ctrl+alt+q"
        assert cfg["recovery_min_gap_s"] == 45.0
    finally:
        interruption.CONFIG_PATH = real
        interruption.reload_config()


def test_defaults_sane():
    cfg = interruption.get_config()
    assert cfg["recovery_lines"], "there must be yield lines by default"
    assert cfg["hotkey"], "the keyboard shush must have a default combo"
    assert cfg["recovery_min_reply_chars"] > 0


# ── wiring tripwires ────────────────────────────────────────────────────────

_APP = Path(__file__).parent.parent / "src" / "app.py"
_REC = Path(__file__).parent.parent / "src" / "core" / "recording.py"


def test_both_stream_paths_yield():
    src = _APP.read_text(encoding="utf-8")
    n = src.count("_interrupt_yield(") - 1     # minus the def itself
    assert n >= 2, f"both stream paths must call _interrupt_yield (found {n})"


def test_typing_cuts_in_instead_of_refusing():
    src = _APP.read_text(encoding="utf-8")
    assert "def _cut_in" in src
    assert "(couldn't cut in" in src, \
        "the refusal text must be replaced by the barge-in path"
    assert "(still finishing the last turn" not in src, \
        "the old outright refusal must be gone"
    assert "interrupt_monitor.trip()" in src


def test_shush_hotkey_registered():
    src = _APP.read_text(encoding="utf-8")
    assert "_shush_her" in src
    assert '"Shush": _shush_hk' in src          # included in the conflict check


def test_monitor_can_be_tripped():
    rec = _REC.read_text(encoding="utf-8")
    assert "def trip" in rec
