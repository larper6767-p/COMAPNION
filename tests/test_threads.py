"""Feature 18: dangling topics — threads she asks about later (pure clock).

core/threads.py is storage + composition only; the LLM extraction lives in
she.py (asserted there only as a static tripwire).
"""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import threads                                      # noqa: E402


NOW = datetime(2026, 11, 3, 14, 0)          # a fixed "right now"


@pytest.fixture()
def store(tmp_path):
    s = threads.ThreadStore(tmp_path / "threads_state.json")
    yield s
    s.save = lambda: None                    # never touch the real state file


def _cfg(**over):
    cfg = dict(threads.DEFAULTS)
    cfg["enabled"] = True
    cfg.update(over)
    return cfg


# ── open / dedupe ─────────────────────────────────────────────────────────────

def test_open_and_list(store):
    assert store.open("your dentist appointment", NOW, _cfg()) is True
    assert len(store) == 1


def test_open_rejects_degenerate_topics(store):
    assert store.open("   ", NOW, _cfg()) is False
    assert store.open("", NOW, _cfg()) is False
    assert len(store) == 0


def test_open_rejects_duplicates_case_and_punctuation(store):
    store.open("The apples interview, tomorrow!", NOW, _cfg())
    assert store.open("the apples interview tomorrow", NOW + timedelta(hours=2),
                      _cfg()) is False
    assert len(store) == 1


def test_take_before_min_age_returns_none(store):
    store.open("dentist", NOW, _cfg())
    assert store.take(NOW + timedelta(hours=2), _cfg()) is None


def test_take_after_min_age_marks_delivered_and_never_repeats(store):
    store.open("dentist", NOW, _cfg())
    later = NOW + timedelta(hours=7)
    t = store.take(later, _cfg())
    assert t is not None and t.topic == "dentist" and t.delivered
    assert store.take(later + timedelta(hours=1), _cfg()) is None


def test_ttl_expires_old_threads(store):
    store.open("dentist", NOW, _cfg())
    cfg = _cfg(ttl_hours=96.0)
    assert store.take(NOW + timedelta(days=10), cfg) is None


def test_oldest_first(store):
    store.open("older matter", NOW, _cfg())
    store.open("newer matter", NOW + timedelta(hours=8), _cfg())
    t = store.take(NOW + timedelta(hours=30), _cfg())
    assert t.topic == "older matter"


def test_latest_opened_is_asked_about_when_older_was_delivered(store):
    store.open("older matter", NOW, _cfg())
    store.open("newer matter", NOW + timedelta(hours=8), _cfg())
    # jump the clock: the older one passes min age first, then it's delivered;
    # the next take must pick the newer one once it, too, is old enough
    store.take(NOW + timedelta(hours=9), _cfg())          # 'older matter'
    t = store.take(NOW + timedelta(hours=10), _cfg())
    assert t is None                                       # newer is 2 h old
    t = store.take(NOW + timedelta(hours=15), _cfg())
    assert t is not None and t.topic == "newer matter"


def test_delivered_threads_never_block_the_cap_forever(store):
    cfg = _cfg(max_open=2)
    assert store.open("a", NOW, cfg)
    assert store.open("b", NOW, cfg)
    assert store.open("c-take", NOW, cfg)                  # cap hit: LRU drop
    assert len(store) == 2
    t = store.take(NOW + timedelta(hours=7), cfg)
    assert t.topic == "b"                                  # 'a' was dropped


def test_prune_drops_delivered_and_expired(store):
    store.open("soon", NOW, _cfg())
    store.open("will expire", NOW - timedelta(days=10), _cfg())
    t = store.take(NOW + timedelta(hours=8), _cfg())       # 'soon' delivered
    assert t is not None
    dropped = store.prune(NOW + timedelta(days=1), _cfg())
    assert dropped >= 2
    assert len(store) == 0


# ── persistence ──────────────────────────────────────────────────────────────

def test_store_survives_a_reload(tmp_path):
    p = tmp_path / "threads_state.json"
    s1 = threads.ThreadStore(p)
    s1.open("the conference talk", NOW, _cfg())
    s2 = threads.ThreadStore(p)
    t = s2.take(NOW + timedelta(hours=8), _cfg())
    assert t is not None and t.topic == "the conference talk"
    # delivery is persisted too
    s3 = threads.ThreadStore(p)
    assert s3.take(NOW + timedelta(hours=9), _cfg()) is None


def test_corrupt_store_file_is_not_fatal(tmp_path):
    p = tmp_path / "threads_state.json"
    p.write_text("{ not json ]", encoding="utf-8")
    s = threads.ThreadStore(p)
    assert len(s) == 0


def test_file_contents_shape(tmp_path):
    p = tmp_path / "t.json"
    s = threads.ThreadStore(p)
    s.open("Vac", NOW, _cfg())
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(raw["threads"], list)
    assert set(raw["threads"][0]) >= {"topic", "opened_at", "delivered"}


# ── question composition ─────────────────────────────────────────────────────

def test_ask_line_injects_the_topic():
    cfg = _cfg()
    line = threads.ask_line("the dentist", cfg, rng=random.Random(0))
    assert "the dentist" in line
    assert "{" not in line


def test_ask_line_pool_means_variety():
    cfg = _cfg()
    got = {threads.ask_line("vacation", cfg,
                            rng=random.Random(i)) for i in range(50)}
    assert len(got) > 1


# ── extraction parser ────────────────────────────────────────────────────────

def test_parse_extraction_json():
    assert threads.parse_extraction('{"thread": "the dentist visit"}') \
        == "the dentist visit"
    assert threads.parse_extraction('```json\n{"thread": null}\n```') is None
    assert threads.parse_extraction("none") is None
    assert threads.parse_extraction("NOTHING.") is None
    assert threads.parse_extraction("") is None
    assert threads.parse_extraction("a stray plumbing question") \
        == "a stray plumbing question"


# ── config gate ──────────────────────────────────────────────────────────────

def test_disabled_means_nothing_is_deliverable(store, tmp_path):
    import yaml
    p = tmp_path / "threads.yaml"
    p.write_text(yaml.safe_dump({"enabled": False}), encoding="utf-8")
    real = threads.CONFIG_PATH
    threads.CONFIG_PATH = p
    try:
        cfg = threads.reload_config()
        store.open("something", NOW, cfg)
        assert store.deliverable(NOW + timedelta(days=3), cfg) == []
        assert store.take(NOW + timedelta(days=3), cfg) is None
    finally:
        threads.CONFIG_PATH = real
        threads.reload_config()


# ── wiring tripwires ────────────────────────────────────────────────────────

_APP = Path(__file__).parent.parent / "src" / "app.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"


def test_she_extracts_threads_after_each_turn():
    src = _SHE.read_text(encoding="utf-8")
    assert "def _extract_threads" in src


def test_app_calls_thread_followup_before_agenda_and_pools():
    src = _APP.read_text(encoding="utf-8")
    call = (self_marker := "self._thread_followup() or self._agenda_excuse()"
            " or self._choose_proactive()")
    assert "".join(call.split()) in "".join(src.split())
