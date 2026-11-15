"""Feature 27b tests: same-day restart continuity.

The rolling conversation window lives in RAM, so a mid-day restart used to
wipe the whole session.  This feature replays the recent tail of TODAY'S
episode log into ``history`` at launch.  Pure + light tests only: the selection
helpers, the loader knob, the real episode store path, and the wiring into
``VoiceCompanion.__init__`` (the companion is built with ``object.__new__``,
the repo's standard way of testing without whisper/Fish/Qt).
"""
import json
import sys
from pathlib import Path

import pytest

# test_headless.py stubs core.* at import time; drop poisoned entries first.
for _m in ("she", "core", "core.aliveness", "core.memory", "core.config"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import aliveness                                            # noqa: E402


ROOT = Path(__file__).parent.parent


def _purge_stubs():
    for _name in list(sys.modules):
        if _name == "she" or _name == "core" or _name.startswith("core."):
            sys.modules.pop(_name, None)


# ── loader knob ───────────────────────────────────────────────────────────────

def test_replay_knob_reads_int_from_yaml(tmp_path):
    p = tmp_path / "aliveness.yaml"
    p.write_text("enabled: true\nrestart_history: 40\n", encoding="utf-8")
    assert aliveness.load_config(p, force=True)["restart_history"] == 40


def test_replay_knob_defaults_to_zero_and_tolerates_garbage(tmp_path):
    missing = aliveness.load_config(tmp_path / "nope.yaml")
    assert missing["restart_history"] == 0

    p = tmp_path / "aliveness.yaml"
    p.write_text("enabled: true\nrestart_history: banana\n", encoding="utf-8")
    assert aliveness.load_config(p, force=True)["restart_history"] == 0
    p.write_text("enabled: true\nrestart_history: -5\n", encoding="utf-8")
    assert aliveness.load_config(p, force=True)["restart_history"] == 0


def test_live_yaml_has_restart_replay_enabled():
    assert aliveness.load_config(force=True)["restart_history"] >= 1


# ── select_replay (pure) ──────────────────────────────────────────────────────

def test_select_replay_keeps_the_tail_oldest_first():
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    out = aliveness.select_replay(msgs, 4)
    assert [m["content"] for m in out] == ["m6", "m7", "m8", "m9"]


def test_select_replay_drops_empty_and_non_turn_entries():
    msgs = [
        {"role": "system", "content": "noise"},
        {"role": "user", "content": ""},          # empty text
        {"role": "proactive", "content": "not a turn"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "   "},       # whitespace only
        {"role": "user", "content": "real"},
    ]
    assert aliveness.select_replay(msgs, 10) == [{
        "role": "assistant", "content": "hello",
    }, {"role": "user", "content": "real"}]


def test_select_replay_caps_at_the_summariser_ceiling():
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    out = aliveness.select_replay(msgs, 100, ceiling=40)
    assert len(out) == 40 and out[0]["content"] == "m10"


def test_select_replay_garbage_is_safe():
    for keep in ("x", None, -3, 0):
        assert aliveness.select_replay([], keep) == []
    assert aliveness.select_replay([{"role": "user", "content": "hi"}], "x") == []
    assert aliveness.select_replay(None, 5) == []


# ── last_exchange_pair (pure) ────────────────────────────────────────────────

def test_last_exchange_pair_finds_the_closing_turn():
    msgs = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "last q"},
        {"role": "assistant", "content": "last a"},
    ]
    assert aliveness.last_exchange_pair(msgs) == ("last q", "last a")


def test_last_exchange_pair_returns_none_without_a_pair():
    assert aliveness.last_exchange_pair([]) is None
    assert aliveness.last_exchange_pair(
        [{"role": "user", "content": "unanswered"}]) is None
    assert aliveness.last_exchange_pair(
        [{"role": "assistant", "content": "orphan"}]) is None


# ── the real disk path ────────────────────────────────────────────────────────

@pytest.fixture
def config_dir(tmp_path):
    """A minimal valid config dir with the replay knob at its shipped value."""
    (tmp_path / "personality.json").write_text(json.dumps({
        "name": "Test", "system_prompt": "x", "greeting": "hi",
        "temperature": 0.7, "max_reply_length": 100,
    }), encoding="utf-8")
    (tmp_path / "aliveness.yaml").write_text(
        "enabled: true\nrestart_history: 40\n", encoding="utf-8")
    return tmp_path


def _episode_file(config_dir):
    from core.memory import MemoryManager
    return MemoryManager(config_dir, tiers={}).episode_store


def test_today_messages_feed_the_replay(config_dir):
    store = _episode_file(config_dir)
    store.record("assistant", "good evening")
    store.record("user", "how was your day")
    store.record("assistant", "long one — showings all afternoon")

    _purge_stubs()
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    from core.config import Config
    obj.config = Config(config_dir)
    obj.memory = type("M", (), {"episode_store": _episode_file(config_dir)})()
    obj.history = []
    obj.last_exchange = None

    restored = VoiceCompanion._load_today_history(obj)
    assert restored == 3
    assert [m["content"] for m in obj.history] == [
        "good evening", "how was your day",
        "long one — showings all afternoon"]
    assert obj.last_exchange == (
        "how was your day", "long one — showings all afternoon")


def test_replay_disabled_without_the_knob(config_dir):
    _purge_stubs()
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    from core.config import Config
    from core.memory import MemoryManager
    obj.config = Config(config_dir)
    obj.config.aliveness_file = config_dir / "aliveness.yaml"
    obj.config.aliveness_file.write_text(
        "enabled: true\nrestart_history: 0\n", encoding="utf-8")
    obj.memory = MemoryManager(config_dir, tiers={})
    obj.memory.episode_store.record("user", "hello again")
    obj.history = []
    obj.last_exchange = None

    assert VoiceCompanion._load_today_history(obj) == 0
    assert obj.history == []


def test_replay_is_capped_and_never_trips_the_summariser(config_dir):
    _purge_stubs()
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    from core.config import Config
    obj.config = Config(config_dir)
    obj.config.aliveness_file = config_dir / "aliveness.yaml"
    obj.config.aliveness_file.write_text(
        "enabled: true\nrestart_history: 999\n", encoding="utf-8")
    obj.memory = type("M", (), {
        "episode_store": _episode_file(config_dir),
    })()
    for i in range(50):                       # over the 40-message ceiling
        obj.memory.episode_store.record("user", f"m{i}")
    obj.history = []
    obj.last_exchange = None

    restored = VoiceCompanion._load_today_history(obj)
    ceiling = max(obj.config.max_history, obj.config.summarize_after * 2)
    assert restored == ceiling
    assert len(obj.history) == ceiling
    assert obj.history[0]["content"] == f"m{50 - ceiling}"
    assert obj.history[-1]["content"] == "m49"


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_init_calls_the_restore():
    src = (ROOT / "src" / "she.py").read_text(encoding="utf-8")
    assert "self._load_today_history()" in src
    fn_start = src.index("def _load_today_history")
    fn_nxt = src.find("\n    def ", fn_start)
    fn = src[fn_start:fn_nxt]
    assert "today_messages()" in fn
    assert "select_replay" in fn
    assert "last_exchange_pair" in fn
