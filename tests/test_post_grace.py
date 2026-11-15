"""Feature 25 tests: the post-flow grace listening window.

The complaint: she still cut you off when you kept speaking after she
decided you were done.  `recording.record_until_silence` now takes
`post_grace_s`: once the stop fires (plain threshold, or the feature-21
judge saying dispatch), she keeps listening that many extra seconds; if
you speak again inside the window the SAME recording continues (the
cutoff decision is reversed and the judge keeps working); stay quiet and
the stop fires just that many seconds later.  Wired through
she.py::_record_voice off config/listening.yaml (`post_grace_s`, default
0.0 in the core so an absent key = the old behavior exactly).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

for _m in ("core", "core.turn_taking"):
    sys.modules.pop(_m, None)

from core import turn_taking as tt                      # noqa: E402

_REC = Path(__file__).parent.parent / "src" / "core" / "recording.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"
_YAML = Path(__file__).parent.parent / "config" / "listening.yaml"


def test_recorder_signature_has_the_grace():
    import inspect
    src = _REC.read_text(encoding="utf-8")
    assert "post_grace_s: float = 0.0" in src           # default = old behavior
    sys.modules.pop("core.recording", None)             # a stub may be cached
    import core.recording as rec
    assert "post_grace_s" in inspect.signature(
        rec.record_until_silence).parameters


def test_recorder_loop_owns_the_grace_state_machine():
    src = "".join(_REC.read_text(encoding="utf-8").split())
    # the decision is scheduled, not immediate
    assert "stop_scheduled,grace_until=False,0" in src
    assert "stop_scheduled=True" in src
    assert "grace_until=silent+int(" in src
    assert "round(post_grace_s/0.03))" in src
    # speaking again during the window REVERTS the cutoff
    assert "Voiceresumedduringpost-grace" in src
    # the judge skips rechecks while the grace is pending
    assert "andnotstop_scheduled" in src
    # the plain path only breaks instantly when grace is off
    assert "elifpost_grace_s>0:" in src


def test_grace_elapses_then_stops():
    src = "".join(_REC.read_text(encoding="utf-8").split())
    assert "ifsilent>=grace_until:" in src
    assert "post-graceelapsed" in src


def test_turn_taking_carries_the_key():
    assert tt.DEFAULTS["post_grace_s"] == 0.0
    cfg = tt.get_config(force=True)
    assert isinstance(cfg["post_grace_s"], float)


def test_yaml_ships_the_few_extra_seconds():
    text = _YAML.read_text(encoding="utf-8")
    assert "post_grace_s: 3.0" in text
    cfg = tt.get_config(force=True)
    assert cfg["post_grace_s"] == 3.0


def test_she_passes_the_grace_to_the_recorder():
    src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert 'post_grace_s=float(cfg.get("post_grace_s",0.0))' in src
