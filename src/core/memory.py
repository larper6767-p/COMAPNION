"""Episodic memory: timestamped conversation log, relationship progression, closeness tiers."""
import json
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class EpisodeStore:
    """Persists daily conversation episode files under config/episodes/.

    Each file is named YYYY-MM-DD.json and contains a list of episode entries:
        {
            "ts":   "2026-10-09T14:32:01",
            "role": "user" | "assistant",
            "text": "...",
            "mood": "joyful" | null
        }

    Summaries are stored in config/episodes/summaries.json as a list of
        {"date": "YYYY-MM-DD", "summary": "..."}
    """

    def __init__(self, episode_dir: Path):
        self.episode_dir = Path(episode_dir)
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self._summary_file = self.episode_dir / "summaries.json"
        self._lock = threading.Lock()

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(self, role: str, text: str, mood: Optional[str] = None) -> None:
        """Append one turn to today's episode file."""
        entry: Dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "role": role,
            "text": text[:2000],  # hard cap per entry
            "mood": mood,
        }
        path = self._today_file()
        with self._lock:
            episodes = self._read_file(path)
            episodes.append(entry)
            self._write_file(path, episodes)

    def save_summary(self, day: str, summary: str) -> None:
        """Persist a generated summary for a given date string (YYYY-MM-DD)."""
        with self._lock:
            summaries = self._read_file(self._summary_file)
            # Replace existing entry for the same date or append
            summaries = [s for s in summaries if s.get("date") != day]
            summaries.append({"date": day, "summary": summary})
            self._write_file(self._summary_file, summaries)
        logger.info(f"Saved summary for {day}")

    # ── Read ──────────────────────────────────────────────────────────────────

    def today_messages(self) -> List[Dict[str, str]]:
        """Return today's entries as role/content dicts (compatible with LLM history)."""
        raw = self._read_file(self._today_file())
        return [{"role": e["role"], "content": e["text"]} for e in raw]

    def recent_summaries(self, n: int = 5) -> str:
        """Return the last n daily summaries as a formatted string."""
        summaries = self._read_file(self._summary_file)
        recent = summaries[-n:] if len(summaries) > n else summaries
        if not recent:
            return ""
        lines = [f"[{s['date']}] {s['summary']}" for s in recent]
        return "\n".join(lines)

    def get_all_episode_dates(self) -> List[str]:
        """Return list of YYYY-MM-DD strings for which episodes exist."""
        return sorted(
            p.stem for p in self.episode_dir.glob("????-??-??.json")
        )

    def summary_pairs(self) -> List[tuple]:
        """Current ``(date, summary)`` pairs from summaries.json, oldest first.

        Used to mirror the semantic store: a summary was previously embedded
        with a content-derived id, so re-summarising the same day appended a
        new vector instead of replacing the old one.
        """
        raw = self._read_file(self._summary_file)
        out: List[tuple] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            day = str(item.get("date") or "").strip()
            text = str(item.get("summary") or "").strip()
            if day and text:
                out.append((day, text))
        return out

    # ── Private ───────────────────────────────────────────────────────────────

    def _today_file(self) -> Path:
        return self.episode_dir / f"{date.today().isoformat()}.json"

    @staticmethod
    def _read_file(path: Path) -> list:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def _write_file(path: Path, data: list) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


class RelationshipTracker:
    """Tracks closeness score and persists it to disk.

    The score starts at 0 and increments after each successful conversation turn.
    Config-defined closeness_tiers map score thresholds → behaviour hints injected
    into the system prompt.
    """

    _SAVE_KEY = "closeness"

    def __init__(self, save_path: Path, tiers: Dict[str, str]):
        self.save_path = Path(save_path)
        self.tiers = tiers           # {"0": "...", "10": "...", ...}
        self._score: int = 0
        self._lock = threading.Lock()
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def score(self) -> int:
        return self._score

    def increment(self, amount: int = 1) -> None:
        with self._lock:
            self._score = min(self._score + amount, 200)  # soft cap
        self._save()

    def current_hint(self) -> str:
        """Return the behaviour hint for the current score."""
        best = max((int(k) for k in self.tiers if int(k) <= self._score), default=0)
        return self.tiers.get(str(best), "")

    def tier_name(self) -> str:
        """Human-readable tier for display (e.g. 'Acquaintance', 'Close')."""
        score = self._score
        if score < 10:
            return "Strangers"
        if score < 30:
            return "Acquaintances"
        if score < 60:
            return "Friends"
        if score < 100:
            return "Close"
        return "Devoted"

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.save_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._score = int(data.get(self._SAVE_KEY, 0))
            logger.info(f"Closeness score loaded: {self._score} ({self.tier_name()})")
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            self._score = 0

    def _save(self) -> None:
        try:
            # Merge with existing data so we don't clobber other keys
            existing: dict = {}
            try:
                with open(self.save_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            existing[self._SAVE_KEY] = self._score
            with open(self.save_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save closeness score: {e}")


class MemoryManager:
    """Combines flat facts, episodic log, and relationship tracking in one place.

    This is what VoiceCompanion interacts with directly.
    """

    def __init__(
        self,
        config_dir: Path,
        tiers: Dict[str, str],
        closeness_enabled: bool = True,
        vector_store: Any = None,
    ):
        self.config_dir = Path(config_dir)
        self.episode_store = EpisodeStore(self.config_dir / "episodes")
        self.relationship = RelationshipTracker(
            save_path=self.config_dir / "relationship.json",
            tiers=tiers,
        )
        self.closeness_enabled = closeness_enabled
        self._facts: List[str] = []
        # Feature 2: optional semantic memory store. None (default) keeps the
        # exact pre-feature behaviour — nothing is embedded or searched.
        self.vector_store: Any = vector_store
        self._vector_top_k: int = 4

    # ── Vector memory (Feature 2) ─────────────────────────────────────────────

    def attach_vector_store(self, store: Any, top_k: int = 4) -> None:
        """Attach a semantic store; subsequently added facts/summaries embed too."""
        self.vector_store = store
        self._vector_top_k = max(1, int(top_k))

    def relevant_memories(self, query: str, top_k: Optional[int] = None) -> List[str]:
        """Return memory texts semantically relevant to ``query`` (may be empty)."""
        if not query or self.vector_store is None:
            return []
        try:
            hits = self.vector_store.search_memories(
                query, top_k=top_k or self._vector_top_k)
        except Exception as e:
            logger.debug(f"Vector search skipped: {e}")
            return []
        return [h["text"] for h in hits if h.get("text")]

    # ── Facts ─────────────────────────────────────────────────────────────────

    def load_facts(self, facts: List[str]) -> None:
        self._facts = facts

    def get_facts(self) -> List[str]:
        return list(self._facts)

    def add_fact(self, fact: str) -> None:
        if fact not in self._facts:
            self._facts.append(fact)
            # Feature 2: keep the semantic store in sync with new facts.
            if self.vector_store is not None:
                self.vector_store.add_memory(fact, metadata={"kind": "fact"})

    def clear_facts(self) -> None:
        self._facts.clear()
        # Forgetting must also forget the embeddings, or semantic recall would
        # keep resurrecting the facts the user just wiped.
        if self.vector_store is not None:
            try:
                self.vector_store.clear_facts()
            except Exception as e:
                logger.debug("Vector fact clear skipped: %s", e)

    def sync_vector_store(self) -> int:
        """Mirror flat facts and current summaries into the semantic store.

        The flat list and summaries.json are the sources of truth.  This adds
        missing vectors, removes stale fact vectors, and replaces old
        content-addressed summary vectors with one per date.  Idempotent; safe
        to call on every startup.
        """
        if self.vector_store is None:
            return 0
        changed = 0
        try:
            changed += int(self.vector_store.sync_facts(self._facts))
        except Exception as e:
            logger.warning("Vector fact sync skipped: %s", e)
        try:
            changed += int(self.vector_store.sync_summaries(
                self.episode_store.summary_pairs()))
        except Exception as e:
            logger.warning("Vector summary sync skipped: %s", e)
        return changed

    # ── Episodes ──────────────────────────────────────────────────────────────

    def log_turn(self, role: str, text: str, mood: Optional[str] = None) -> None:
        self.episode_store.record(role, text, mood)

    def save_summary(self, day: str, summary: str) -> None:
        self.episode_store.save_summary(day, summary)
        # Feature 2: summaries are recallable across old conversations.
        # The id is per-date, not per-text: re-summarising the same day must
        # replace the previous vector instead of stacking stale variants.
        if self.vector_store is not None and summary:
            self.vector_store.add_memory(
                f"[{day}] {summary}",
                metadata={"kind": "summary", "date": day},
                mem_id=f"summary:{day}",
            )

    def recent_context(self, n_summaries: int = 3) -> str:
        """A paragraph of recent memory to inject into the system prompt."""
        summaries = self.episode_store.recent_summaries(n_summaries)
        if not summaries:
            return ""
        return f"Recent conversation history:\n{summaries}"

    # ── Relationship ──────────────────────────────────────────────────────────

    def after_turn(self) -> None:
        """Call after each successful conversation turn."""
        if self.closeness_enabled:
            self.relationship.increment(1)

    def closeness_hint(self) -> str:
        return self.relationship.current_hint() if self.closeness_enabled else ""

    def closeness_score(self) -> int:
        return self.relationship.score

    def tier_name(self) -> str:
        return self.relationship.tier_name()

    # ── System prompt additions ───────────────────────────────────────────────

    def build_context_block(
        self,
        max_facts: Optional[int] = None,
        query: Optional[str] = None,
    ) -> str:
        """Return a block to append to the system prompt.

        Injects the most recent `max_facts` facts (newest win) to keep the
        context window from bloating as the fact list grows. None = no cap.

        When ``query`` is given and a vector store is attached (Feature 2), the
        memories most semantically relevant to it are appended — the semantic
        complement to the flat fact list. Memories already shown in the fact
        line are not repeated.
        """
        lines: List[str] = []
        injected_facts: List[str] = []

        hint = self.closeness_hint()
        if hint:
            lines.append(f"[Relationship level — {self.tier_name()} ({self.relationship.score} pts)]: {hint}")

        if self._facts:
            facts = self._facts
            if max_facts is not None and len(facts) > max_facts:
                facts = facts[-max_facts:]
            injected_facts = facts
            lines.append("Things you know about the user: " + "; ".join(facts) + ".")

        if query:
            relevant: List[str] = []
            for mem in self.relevant_memories(query):
                if mem not in injected_facts and mem not in relevant:
                    relevant.append(mem)
            if relevant:
                bullet = "\n".join(f"- {m}" for m in relevant)
                lines.append("Relevant context from past conversations:\n" + bullet)

        recent = self.recent_context()
        if recent:
            lines.append(recent)

        return "\n\n".join(lines)
