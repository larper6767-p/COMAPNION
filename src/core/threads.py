"""Feature 18: dangling topics — the things worth asking about later.

What it does
────────────
A person does not drop what you told them.  "I have the dentist on
Thursday" should come back as "how did the dentist go?" at the right
time.  This module is the small store behind that:

* open a thread when it's noticed (extraction lives in she.py; this module
  only stores and asks),
* ``take()`` returns the OLDEST thread whose age is at least
  ``min_age_hours`` and still under ``ttl_hours`` (once delivered, never
  re-asked),
* questions are composed from configured templates with ``{topic}``.

PURE core module: no Qt, no LLM, no wall-clock reads at import — every
method takes an explicit ``now``.  The store is a single JSON file
(``config/threads_state.json``); a corrupt/missing file is tolerated as
"no threads yet".  ``enabled: false`` makes delivery impossible (the old
proactive pools speak instead).
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "threads.yaml"
STATE_PATH = ROOT / "config" / "threads_state.json"

DEFAULTS: Dict[str, Any] = {
    "enabled": True,          # OFF by default: extraction costs an LLM call
    "min_age_hours": 6.0,      # don't ask about "tomorrow" 10 minutes later
    "ttl_hours": 96.0,         # after ~4 days the moment has passed
    "max_open": 5,             # more than that would feel like a checklist
    "max_ask_per_day": 1,      # one check-in per day, tops
    "ask_lines": [
        "By the way — how did {topic} go?",
        "Right, quick check-in: {topic}?",
        "Been meaning to ask — did {topic} work out?",
    ],
}

_cfg_cache: Optional[Dict[str, Any]] = None


# ── config ────────────────────────────────────────────────────────────────────

def reload_config() -> Dict[str, Any]:
    global _cfg_cache
    cfg = dict(DEFAULTS)
    try:
        raw = yaml_safe_load()
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for key in ("enabled",) + tuple(k for k in DEFAULTS if k != "enabled"):
            if key in raw:
                try:
                    if key == "enabled":
                        cfg[key] = bool(raw[key])
                    elif key in ("ask_lines",):
                        lines = raw[key]
                        if isinstance(lines, list) and lines:
                            cfg[key] = [str(x) for x in lines]
                    else:
                        cfg[key] = float(raw[key])
                except Exception:
                    pass
    _cfg_cache = cfg
    return cfg


def yaml_safe_load():
    import yaml
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def get_config(force: bool = False) -> Dict[str, Any]:
    if force or _cfg_cache is None:
        return reload_config()
    return _cfg_cache


normalize_re = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(topic: str) -> str:
    """Loose key so 'the apples interview tomorrow' == 'the apples interview'."""
    t = normalize_re.sub(" ", (topic or "").lower())
    return re.sub(r"\s+", " ", t).strip()


# ── the store ────────────────────────────────────────────────────────────────

class Thread:
    """One open topic worth a follow-up question."""

    def __init__(self, topic: str, opened_at: datetime,
                 delivered: bool = False,
                 delivered_at: Optional[datetime] = None) -> None:
        self.topic = (topic or "").strip()
        self.opened_at = opened_at
        self.delivered = bool(delivered)
        self.delivered_at = delivered_at

    @property
    def age_hours(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds() / 3600.0


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _from_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def parse_extraction(text: str) -> Optional[str]:
    """Parse the extractor model's reply: JSON {"thread": "..."} | null.

    Tolerates code fences and bare-text replies ("none"/empty => None);
    a bare non-empty reply is treated as the topic itself.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    body = (fenced.group(1) if fenced else raw).strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            t = str(data.get("thread") or "")
            return t if t.strip() else None
    except Exception:
        pass
    if re.fullmatch(r"(?i)(none|nothing|n/?a|null|no threads?)\.?", body):
        return None
    if len(body) <= 300 and "\n" not in body:
        return body
    return None


class ThreadStore:
    """Persisted store of open threads (one JSON file)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else STATE_PATH
        self._threads: List[Thread] = []
        self.load()

    # ── persistence ─────────────────────────────────────────────────────
    def load(self) -> None:
        self._threads = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        items = raw.get("threads", []) if isinstance(raw, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            topic = str(it.get("topic", "")).strip()
            if not topic:
                continue
            try:
                opened = _from_iso(str(it.get("opened_at", "")))
            except Exception:
                continue
            threaded = Thread(
                topic, opened,
                delivered=bool(it.get("delivered", False)))
            try:
                if it.get("delivered_at"):
                    threaded.delivered_at = _from_iso(str(it["delivered_at"]))
            except Exception:
                pass
            self._threads.append(threaded)

    def save(self) -> None:
        data = {
            "threads": [
                {"topic": t.topic, "opened_at": _iso(t.opened_at),
                 "delivered": t.delivered,
                 "delivered_at": _iso(t.delivered_at)
                 if t.delivered_at else None}
                for t in self._threads
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    # ── open / take / prune ─────────────────────────────────────────────
    def open(self, topic: str, now: datetime,
             cfg: Optional[Dict[str, Any]] = None) -> bool:
        """Add a thread; False when degenerate or an exact duplicate is open."""
        key = normalize(topic)
        if not key:
            return False
        for t in self._threads:
            if normalize(t.topic) == key and not t.delivered:
                return False            # already open, don't stack it
        if len(self._threads) >= int((cfg or get_config()).get("max_open", 5)):
            # drop the oldest OPEN thread (LRU cap) or skip delivered ones
            if self._threads and self._threads[0].delivered:
                self._threads.pop(0)
            elif self._threads:
                self._threads.pop(0)
        self._threads.append(Thread(topic.strip(), now, False))
        self.save()
        return True

    def deliverable(self, now: datetime,
                    cfg: Optional[Dict[str, Any]] = None) -> List[Thread]:
        """Open threads ready to be asked about (oldest first)."""
        cfg = cfg or get_config()
        if not cfg.get("enabled", False):
            return []
        min_h = float(cfg.get("min_age_hours", 6.0))
        ttl_h = float(cfg.get("ttl_hours", 96.0))
        out = []
        for t in self._threads:
            if t.delivered:
                continue
            age = (now - t.opened_at).total_seconds() / 3600.0
            if age >= min_h and age < ttl_h:
                out.append(t)
        out.sort(key=lambda t: t.opened_at)
        return out

    def take(self, now: datetime,
             cfg: Optional[Dict[str, Any]] = None) -> Optional[Thread]:
        """The oldest deliverable thread, marked DELIVERED (and persisted)."""
        ready = self.deliverable(now, cfg)
        if not ready:
            return None
        t = ready[0]
        t.delivered = True
        t.delivered_at = now
        self.save()
        return t

    def prune(self, now: datetime, cfg: Optional[Dict[str, Any]] = None) -> int:
        """Drop delivered and expired threads; returns how many were dropped."""
        cfg = cfg or get_config()
        ttl_h = float(cfg.get("ttl_hours", 96.0))
        keep = []
        dropped = 0
        for t in self._threads:
            age = (now - t.opened_at).total_seconds() / 3600.0
            if t.delivered or age >= ttl_h:
                dropped += 1
            else:
                keep.append(t)
        if dropped:
            self._threads = keep
            self.save()
        return dropped

    def __len__(self) -> int:
        return len(self._threads)


def ask_line(topic: str, cfg: Optional[Dict[str, Any]] = None,
             rng: Optional[random.Random] = None) -> str:
    """Compose the check-in question from the configured templates."""
    cfg = cfg or get_config()
    lines = cfg.get("ask_lines") or DEFAULTS["ask_lines"]
    rng = rng if rng is not None else random.Random()
    return str(rng.choice(lines)).format(topic=(topic or "").strip())


__all__ = [
    "CONFIG_PATH", "STATE_PATH", "DEFAULTS",
    "Thread", "ThreadStore", "ask_line", "normalize", "parse_extraction",
    "get_config", "reload_config",
]
