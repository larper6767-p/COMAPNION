"""Feature 20: web search — decision parsing, cache, formatting + wiring.

core/websearch.py is the pure engine (network injected in tests); the LLM
classification and the injection into the turn live in she.py/app.py and
are asserted as static tripwires only.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import websearch as ws                       # noqa: E402

NOW = datetime(2026, 11, 3, 14, 0)

_APP = Path(__file__).parent.parent / "src" / "app.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"


# ── decision parsing ──────────────────────────────────────────────────────────

def test_parse_json_yes():
    raw = '{"search": true, "query": "bts concert ticket price"}'
    assert ws.parse_decision(raw) == (True, "bts concert ticket price")


def test_parse_json_yes_with_leading_chatter():
    raw = 'Sure! {"search": true, "query": "berlin weather today"} done.'
    assert ws.parse_decision(raw) == (True, "berlin weather today")


def test_parse_json_no():
    assert ws.parse_decision('{"search": false}') == (False, "")
    assert ws.parse_decision('{"search": true, "query": ""}') == (False, "")


def test_parse_bracket_fallback():
    assert ws.parse_decision("[SEARCH: latest nvidia gpu price]") == \
        (True, "latest nvidia gpu price")


def test_parse_junk_and_empty_are_no():
    assert ws.parse_decision("") == (False, "")
    assert ws.parse_decision("I think yes but nobody said why") == (False, "")
    assert ws.parse_decision(None) == (False, "")


def test_parse_long_query_is_truncated():
    long_q = '{"search": true, "query": "' + ("w" * 300) + '"}'
    want, q = ws.parse_decision(long_q)
    assert want and len(q) <= ws.MAX_QUERY_CHARS


def test_parse_collapse_inner_whitespace():
    want, q = ws.parse_decision('{"search": true, "query": "openai   news\n" }')
    assert want and q == "openai news"


# ── perform: shaping + caching + failure ──────────────────────────────────────

def _fake_fetch_factory(hits, cache_calls):
    def fetch(query, max_results, timeout_s):
        cache_calls.append(query)
        return list(hits)
    return fetch


_RAW = [{"title": "Result One", "href": "https://a.io/x", "body": "Body text"},
        {"title": "Result Two", "href": "https://b.io/y", "body": "Snip"}]


def test_perform_shapes_provider_rows():
    calls = []
    got = ws.perform("berlin weather", {"max_results": 5}, now=NOW,
                     fetch=_fake_fetch_factory(_RAW, calls))
    assert got == [{"title": "Result One", "url": "https://a.io/x",
                    "snippet": "Body text"},
                   {"title": "Result Two", "url": "https://b.io/y",
                    "snippet": "Snip"}]


def test_perform_respects_max_results():
    calls = []
    rows = [dict(_RAW[0]) for _ in range(9)]
    got = ws.perform("q", {"max_results": 3}, now=NOW,
                     fetch=_fake_fetch_factory(rows, calls))
    assert len(got) == 3


def test_perform_empty_query_never_searches():
    calls = []
    assert ws.perform("   ", {}, now=NOW,
                      fetch=_fake_fetch_factory(_RAW, calls)) == []
    assert calls == []


def test_perform_caches_identical_query(tmp_path):
    calls = []
    cache = ws.SearchCache(tmp_path / "cache.json")
    cfg = {"max_results": 5, "cache_hours": 3.0}
    ws.perform("berlin weather", cfg, cache=cache, now=NOW,
               fetch=_fake_fetch_factory(_RAW, calls))
    ws.perform("berlin  weather?", cfg, cache=cache, now=NOW + timedelta(hours=1),
               fetch=_fake_fetch_factory(_RAW, calls))
    # one FETCH call only: the second (normalized-identical) question was
    # answered from the cache without touching the provider again.
    assert calls == ["berlin weather"]


def test_perform_refetches_after_ttl_expiry(tmp_path):
    calls = []
    cache = ws.SearchCache(tmp_path / "cache.json")
    cfg = {"max_results": 5, "cache_hours": 3.0}
    ws.perform("berlin weather", cfg, cache=cache, now=NOW,
               fetch=_fake_fetch_factory(_RAW, calls))
    ws.perform("berlin weather", cfg, cache=cache,
               now=NOW + timedelta(hours=4),
               fetch=_fake_fetch_factory(_RAW, calls))
    assert len(calls) == 2


def test_perform_never_caches_a_failure(tmp_path):
    calls = []
    cache = ws.SearchCache(tmp_path / "cache.json")
    cfg = {"max_results": 5, "cache_hours": 3.0}
    ws.perform("berlin weather", cfg, cache=cache, now=NOW,
               fetch=_fake_fetch_factory([], calls))
    ws.perform("berlin weather", cfg, cache=cache, now=NOW + timedelta(hours=1),
               fetch=_fake_fetch_factory(_RAW, calls))
    # the empty result did NOT stick: the next ask searched again
    assert len(calls) == 2


def test_perform_swallows_raising_fetch(tmp_path):
    def boom(query, max_results, timeout_s):
        raise OSError("network down")
    assert ws.perform("q", {}, now=NOW, fetch=boom) == []


def test_search_cache_tolerates_a_corrupt_file(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("not json at all{", encoding="utf-8")
    cache = ws.SearchCache(p)
    assert cache.get("k", NOW, 3.0) is None
    cache.put("k", [], NOW)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 1


# ── formatting ────────────────────────────────────────────────────────────────

def test_format_results_carries_date_and_items():
    block = ws.format_results("berlin weather", ws.perform(
        "berlin weather", {"max_results": 5}, now=NOW,
        fetch=_fake_fetch_factory(_RAW, [])), now=NOW)
    assert "2026-11-03" in block
    assert "1. Result One" in block and "2. Result Two" in block
    assert "https://a.io/x" in block
    assert "berlin weather" in block


def test_format_results_unavailable_block():
    block = ws.format_results("berlin weather", [], now=NOW)
    assert "couldn't" in block.lower()
    assert "do not invent fresh numbers" in block


# ── import safety + config defaults ──────────────────────────────────────────

def test_importing_the_module_pulls_no_search_dependency():
    import importlib
    importlib.import_module("core.websearch")
    assert "ddgs" not in sys.modules
    assert "duckduckgo_search" not in sys.modules


def test_enabled_is_off_by_default():
    assert ws.DEFAULTS["enabled"] is False


def test_live_config_file_parses_with_known_types():
    cfg = ws.get_config(force=True)
    assert isinstance(cfg["enabled"], bool)
    assert 1 <= int(cfg["max_results"]) <= 20
    assert float(cfg["cache_hours"]) > 0


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_she_asks_the_web_question_per_turn():
    src = _SHE.read_text(encoding="utf-8")
    assert "def _web_context" in src
    assert "from core import websearch as _web" in src
    assert "_web.get_config()" in src
    assert "_web.parse_decision(full)" in src
    assert "_web.SearchCache(state_dir=" in src


def test_app_injects_web_block_into_system_message():
    src = "".join(_APP.read_text(encoding="utf-8").split())
    assert "web_block=self._web_context(text)" in src
    assert "messages=self.build_messages(" in src
    assert "web_block=web_block" in src
    # the assembly point records the web section for prompt observability
    she_src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert 'self._add_prompt_section("web",block)' in she_src
