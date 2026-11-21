"""Feature 20: internet access — a simple web search she can choose to run.

What it does
────────────
A real person doesn't know last night's score or today's weather — she
looks it up.  This module is the small engine behind that:

* a tiny per-turn classification (in she.py) decides whether this user
  message wants fresh web info at all,
* when it does, one DDGS (DuckDuckGo) query runs and the top results are
  injected as extra SYSTEM context for THAT turn only,
* identical queries are served from a small on-disk TTL cache
  (``config/websearch_cache.json``) so a repeat question never re-hits
  the provider,
* any failure degrades to "couldn't look it up" guidance instead of a
  broken turn.

PURE core module: no Qt, no LLM, no wall-clock reads at import — every
function that cares about time takes an explicit ``now``.  The optional
``ddgs`` package (``duckduckgo_search``) is imported lazily INSIDE the
default fetcher, so importing this module is dependency- and network-free
(the test suite stays headless).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "websearch.yaml"
CACHE_PATH = ROOT / "state" / "websearch_cache.json"

DEFAULTS: Dict[str, Any] = {
    "enabled": False,       # OFF by default: decisions cost an LLM call per turn
    "max_results": 5,       # snippets injected into her context (3..8 is sane)
    "timeout_s": 8.0,       # per search, seconds
    "cache_hours": 3.0,     # same query answered from cache within this window
    "max_entries": 200,     # cache rows kept on disk at most
}

MAX_QUERY_CHARS = 80

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

def yaml_safe_load():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = dict(DEFAULTS)
    try:
        raw = yaml_safe_load()
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key in raw:
                try:
                    if key == "enabled":
                        cfg[key] = bool(raw[key])
                    elif key == "max_entries":
                        cfg[key] = int(raw[key])
                    else:
                        cfg[key] = float(raw[key])
                except Exception:
                    pass
    _cfg_cache = cfg
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


normalize_re = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(query: str) -> str:
    """Loose key so 'weather in berlin?' == 'weather in berlin'."""
    t = normalize_re.sub(" ", (query or "").lower())
    return re.sub(r"\s+", " ", t).strip()


# ── decision parsing (she.py's classifier output shape) ──────────────────────

def parse_decision(raw: str) -> Tuple[bool, str]:
    """Parse the classifier's reply into (search?, query).

    The prompt says "reply ONLY with JSON", but models drift: accept the
    first JSON-ish brace block, and a "[SEARCH: query]" bracket fallback.
    Anything else is "no".
    """
    t = (raw or "").strip()
    if not t:
        return False, ""

    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        block = m.group(0)
        data: Any = None
        try:
            data = json.loads(block)
        except Exception:
            # Models sometimes emit pretty-printed / multi-line JSON with
            # literal control characters inside a value: salvage the two
            # fields we actually need instead of giving up entirely.
            try:
                want_m = re.search(r'"search"\s*:\s*(true|false)', block,
                                   re.IGNORECASE)
                q_m = re.search(r'"query"\s*:\s*"([^"]*)"', block, re.DOTALL)
                if want_m or q_m:
                    data = {"search": bool(want_m and
                                           want_m.group(1).lower() == "true"),
                            "query": (q_m.group(1) if q_m else "")}
            except Exception:
                data = None
        if isinstance(data, dict) and bool(data.get("search")):
            q = str(data.get("query") or "").strip()
            q = re.sub(r"\s+", " ", q).strip(" '\"")[:MAX_QUERY_CHARS].strip()
            return (bool(q), q)
        return False, ""

    m = re.search(r"\[\s*search\s*[:\-]\s*(.+?)\]", t, re.IGNORECASE)
    if m:                                   # bracket fallback: [SEARCH: bts]
        q = re.sub(r"\s+", " ", m.group(1)).strip(" '\"")[:MAX_QUERY_CHARS].strip()
        return (bool(q), q)

    return False, ""


# ── the fetcher ───────────────────────────────────────────────────────────────

def _clean_hit(h: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Shape one provider row into {title, url, snippet}, trimmed."""
    try:
        title = str(h.get("title") or "").strip()
        url = str(h.get("href") or h.get("url") or "").strip()
        snippet = str(h.get("body") or h.get("snippet") or "").strip()
        if not (title or snippet):
            return None
        return {"title": title[:110], "url": url[:200],
                "snippet": snippet[:280]}
    except Exception:
        return None


def _normalized_hits(raw_hits: Any, max_results: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not raw_hits:
        return out
    for h in raw_hits:
        if len(out) >= max_results:
            break
        try:                                    # generators in some versions
            clean = _clean_hit(h if isinstance(h, dict) else dict(h or {}))
        except Exception:
            clean = None
        if clean:
            out.append(clean)
    return out


def _default_fetch(query: str, max_results: int, timeout_s: float
                   ) -> List[Dict[str, str]]:
    """Run one DuckDuckGo search.  Returns [] on ANY failure — never raises.

    ``ddgs`` (formerly ``duckduckgo_search``) is imported lazily here so
    importing this module needs neither the package nor the network.
    The v2..v9 APIs all differ slightly in argument names; try the modern
    keyword shape first, then a positional fallback, and always cap.
    """
    try:
        try:
            from ddgs import DDGS                       # prefer the new name
        except ImportError:
            from duckduckgo_search import DDGS          # old name
    except Exception:
        return []

    try:
        dd = DDGS()
        try:
            raw = dd.text(query, max_results=max_results, timeout=timeout_s)
        except TypeError:
            try:
                raw = dd.text(query, max_results=max_results)
            except TypeError:
                raw = dd.text(query)
        if hasattr(raw, "__next__"):            # some versions stream
            rows = []
            for row in raw:
                rows.append(row)
                if len(rows) >= max_results:
                    break
        else:
            rows = list(raw or [])
    except Exception:
        return []
    return rows


def perform(query: str, cfg: Optional[Dict[str, Any]] = None,
            cache: Optional["SearchCache"] = None, now: Optional[datetime] = None,
            fetch=None) -> List[Dict[str, str]]:
    """One search, cache-aware.  NEVER caches empty results (a rate limit
    or an offline moment shouldn't stick).  Inject ``fetch`` in tests."""
    cfg = cfg or {}
    now = now or datetime.now()
    q = (query or "").strip()
    if not q:
        return []

    max_results = max(1, min(20, int(cfg.get("max_results", 5))))
    ttl_h = float(cfg.get("cache_hours", 3.0))
    key = normalize(q)

    if cache is not None:
        cached = cache.get(key, now, ttl_h)
        if cached is not None:
            return cached

    if fetch is None:
        fetch = _default_fetch
    try:
        raw = fetch(q, max_results, float(cfg.get("timeout_s", 8.0)))
    except Exception:
        raw = []
    hits = _normalized_hits(raw, max_results)

    if hits and cache is not None:
        cache.put(key, hits, now)
    return hits


# ── the on-disk TTL cache ─────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _from_iso(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return datetime.min


class SearchCache:
    """config/websearch_cache.json — {key: {t: iso, hits: [...]}} rows."""

    def __init__(
        self,
        path: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        runtime_dir: Optional[Path] = None,
    ) -> None:
        if path is None:
            if state_dir is None:
                state_dir = data_dir if data_dir is not None else runtime_dir
            if state_dir is None and config_dir is not None:
                state_dir = config_dir
            if state_dir is not None:
                path = Path(state_dir) / "websearch_cache.json"
            else:
                path = CACHE_PATH
        self.path = Path(path)
        if state_dir is not None and config_dir is not None:
            legacy = Path(config_dir) / "websearch_cache.json"
            if legacy.is_file() and not self.path.exists():
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(legacy, self.path)
                except OSError:
                    pass

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save(self, entries: Dict[str, Any], max_entries: int) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                ordered = sorted(entries.items(),
                                 key=lambda kv: _from_iso(kv[1].get("t", "")),
                                 reverse=True)
            except Exception:
                ordered = list(entries.items())
            if len(ordered) > max_entries:
                ordered = ordered[:max_entries]
            self.path.write_text(
                json.dumps({"version": 1, "entries": dict(ordered)},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass                                  # a cache write must never bite

    def get(self, key: str, now: datetime, ttl_hours: float
            ) -> Optional[List[Dict[str, str]]]:
        row = self._load().get("entries", {}).get(key)
        if not row:
            return None
        t = _from_iso(row.get("t", ""))
        if (now - t).total_seconds() > max(0.01, ttl_hours) * 3600.0:
            return None
        hits = row.get("hits")
        if isinstance(hits, list) and hits:
            return hits                              # already cleaned/sized
        return None

    def put(self, key: str, hits: List[Dict[str, str]], now: datetime) -> None:
        try:
            max_entries = int(get_config().get("max_entries", 200))
        except Exception:
            max_entries = 200
        entries = self._load().get("entries", {})
        if not isinstance(entries, dict):
            entries = {}
        entries[key] = {"t": _iso(now), "hits": hits}
        self._save(entries, max_entries)


# ── context rendering ─────────────────────────────────────────────────────────

def format_results(query: str, hits: List[Dict[str, str]],
                   now: Optional[datetime] = None,
                   cfg: Optional[Dict[str, Any]] = None) -> str:
    """The SYSTEM-context block handed to her for this turn only."""
    now = now or datetime.now()
    if not hits:
        return (f"[web search unavailable] You tried the internet for "
                f"{query!r} and got nothing this time. Say briefly that you "
                "couldn't look it up, do not invent fresh numbers, and "
                "answer from memory only if that is safe.")
    lines = [
        f"[web results] Today is {now:%Y-%m-%d %H:%M}. You looked up "
        f"{query!r}. Base anything time-sensitive on these snippets; "
        "don't invent extra facts, and never cite a source you didn't "
        "see here:",
    ]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}\n   {h['snippet']}\n   {h['url']}")
    return "\n".join(lines)
