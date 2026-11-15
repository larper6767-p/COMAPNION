"""Tests for the intelligence/companion upgrades.

Run with:
    $env:QT_QPA_PLATFORM="offscreen"; $env:SDL_AUDIODRIVER="dummy"
    python -m pytest tests/test_intelligence.py -v

These tests touch only light modules (core.config, core.memory) and do not
require hardware, LLM keys, or the GUI. All writes go to pytest's tmp_path.
"""
import sys
import json
from pathlib import Path

import pytest

# When pytest collects both test files in one session, test_headless.py's module-
# level stubbing overwrites sys.modules['core.config'] / ['core.memory'] with fake
# classes. Pop those poisoned entries first so the real implementations load here.
for _m in ("core", "core.config", "core.memory"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def empty_config(tmp_path):
    """A minimal valid personality.json in a temp config dir."""
    (tmp_path / "personality.json").write_text(json.dumps({
        "name": "Test", "system_prompt": "x", "greeting": "hi",
        "temperature": 0.7, "max_reply_length": 100,
    }), encoding="utf-8")
    return tmp_path


# ── Config defaults ─────────────────────────────────────────────────────────────

def test_inte_smart_config_defaults(empty_config):
    """The new intelligence/companion switches default to enabled."""
    from core.config import Config
    c = Config(config_dir=empty_config)
    # Phase 3d: proactive messages default to the state-aware static pools
    # (no LLM call per message), so proactive_generate is opt-in OFF by default.
    assert c.proactive_generate is False
    assert c.auto_extract_facts is True
    assert c.gentle_missed is True


def test_inte_personality_overrides_smart_flags(tmp_path):
    """personality.json values (false) must override the smart defaults."""
    from core.config import Config
    (tmp_path / "personality.json").write_text(json.dumps({
        "name": "T", "system_prompt": "x", "greeting": "hi",
        "temperature": 0.7, "max_reply_length": 100,
        "proactive_generate": False,
        "auto_extract_facts": False,
        "gentle_missed": False,
    }), encoding="utf-8")
    c = Config(config_dir=tmp_path)
    assert c.proactive_generate is False
    assert c.auto_extract_facts is False
    assert c.gentle_missed is False


# ── Mood logging into episodes ─────────────────────────────────────────────────

def test_inte_episode_logs_mood(tmp_path):
    """log_turn must persist an optional mood alongside the text."""
    from core.memory import EpisodeStore
    store = EpisodeStore(tmp_path)
    store.record("assistant", "Hello!", mood="joyful")
    day = __import__("datetime").date.today().isoformat()
    entries = json.loads((tmp_path / f"{day}.json").read_text(encoding="utf-8"))
    assert entries[-1]["role"] == "assistant"
    assert entries[-1]["text"] == "Hello!"
    assert entries[-1]["mood"] == "joyful"


def test_inte_episode_mood_defaults_none(tmp_path):
    """log_turn with no mood must store null, not crash."""
    from core.memory import EpisodeStore
    store = EpisodeStore(tmp_path)
    store.record("user", "plain turn")
    day = __import__("datetime").date.today().isoformat()
    entries = json.loads((tmp_path / f"{day}.json").read_text(encoding="utf-8"))
    assert entries[-1]["mood"] is None


# ── Fact dedup ────────────────────────────────────────────────────────────────

def test_inte_fact_dedup(tmp_path):
    """Adding an existing fact must not create a duplicate."""
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.add_fact("user likes coffee")
    m.add_fact("user likes coffee")
    assert m.get_facts().count("user likes coffee") == 1


# ── max_memory_facts config ────────────────────────────────────────────────────

def test_inte_max_memory_facts_default(empty_config):
    """max_memory_facts defaults to a sane cap."""
    from core.config import Config
    c = Config(config_dir=empty_config)
    assert c.max_memory_facts == 20


def test_inte_max_memory_facts_override(tmp_path):
    """personality.json can tune the fact-injection cap."""
    from core.config import Config
    (tmp_path / "personality.json").write_text(json.dumps({
        "name": "T", "system_prompt": "x", "greeting": "hi",
        "temperature": 0.7, "max_reply_length": 100,
        "max_memory_facts": 5,
    }), encoding="utf-8")
    c = Config(config_dir=tmp_path)
    assert c.max_memory_facts == 5


# ── Fact injection capping ─────────────────────────────────────────────────────

def test_inte_context_block_caps_facts(tmp_path):
    """build_context_block must inject only the most recent max_facts."""
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    for i in range(10):
        m.add_fact(f"fact {i}")
    block = m.build_context_block(max_facts=3)
    # Newest facts win; oldest are trimmed.
    assert "fact 0" not in block
    assert "fact 7" in block and "fact 9" in block


def test_inte_context_block_no_cap(tmp_path):
    """Without a cap (None) every fact is injected."""
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    for i in range(5):
        m.add_fact(f"fact {i}")
    block = m.build_context_block()
    assert "fact 0" in block
    assert "fact 4" in block
