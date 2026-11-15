"""Vector-based long-term memory (Feature 2).

Backed by **chromadb** with a local, on-disk store.  Everything here imports
chromadb *lazily* and degrades to a no-op :class:`NullVectorStore` on any
failure, so a disabled or broken feature leaves the companion byte-identical to
before.

Public API
----------
``create_vector_store(settings=None, ...)``
    Factory. Returns a real :class:`VectorStore` when enabled and initialisable,
    otherwise a :class:`NullVectorStore`.  Never raises.
``VectorStore`` / ``NullVectorStore``
    ``add_memory`` / ``search_memories`` / ``migrate_from_json`` / ``count``.
``HashingEmbeddingFunction``
    Deterministic, offline embeddings (no model download) — used by
    ``embedding: hash`` and by the tests.
``load_config``
    Read ``config/memory.yaml`` merged over safe defaults (feature off).

Embedding backends
------------------
* ``embedding: default`` — chromadb's bundled ONNX ``all-MiniLM-L6-v2``
  (one-time ~80 MB download, cached under ``~/.cache/chroma``).
* ``embedding: hash`` — a dependency-free hashed bag-of-words.  No download,
  fully offline; lower quality, handy for tests / air-gapped runs.

Notes
-----
* We always pass *explicit* embeddings to chromadb and never rely on its
  embedding-function plumbing, so the store works the same for either backend
  and there is no custom-EF protocol to satisfy.
* IDs are deterministic (``stable_id``), and writes use ``upsert``, so
  migration is idempotent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "memory.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "backend": "chromadb",
    # Persistence dir, relative to the project root unless absolute.
    "path": "memory_store",
    "collection": "evelyn_memories",
    # "default" = ONNX all-MiniLM-L6-v2 (downloads once) | "hash" = offline
    "embedding": "default",
    "hash_dim": 256,
    # How many memories to inject into the prompt per turn.
    "top_k": 4,
    # Optional cosine/L2 distance ceiling for a hit to count (None = keep all).
    "max_distance": None,
    # chromadb's anonymised telemetry — off by default for a local pet.
    "telemetry": False,
}


# ── helpers ───────────────────────────────────────────────────────────────────

def stable_id(kind: str, text: str) -> str:
    """Deterministic id for a memory, so re-adding/migrating never duplicates."""
    digest = hashlib.sha1(f"{kind}\x1f{text}".encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:40]}"


def _resolve_path(value: Any) -> Path:
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``config/memory.yaml`` merged over defaults; never raises.

    A missing/malformed file (or missing PyYAML) yields pure defaults, i.e. the
    feature stays OFF — a bad config can never break startup.
    """
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        import yaml  # local: only needed when a config file exists

        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except Exception as e:
        logger.warning("memory.yaml unreadable; using defaults: %s", e)
        return cfg

    if isinstance(raw, dict):
        for key in DEFAULT_CONFIG:
            if key in raw:
                cfg[key] = raw[key]
    return cfg


# ── embedding backends ────────────────────────────────────────────────────────

class HashingEmbeddingFunction:
    """Deterministic, offline bag-of-words embedding (L2-normalised).

    Not semantically strong, but dependency-free and reproducible — ideal for
    tests and air-gapped runs.  Tokens are lowercased alphanumerics hashed into
    a fixed-width vector.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = int(dim)

    def name(self) -> str:                       # chromadb EF protocol
        return "marin-hash"

    def get_config(self) -> Dict[str, Any]:
        return {"dim": self.dim}

    @classmethod
    def build_from_config(cls, config: Dict[str, Any]) -> "HashingEmbeddingFunction":
        return cls(dim=int((config or {}).get("dim", 256)))

    def __call__(self, input: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in input:
            vec = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
                idx = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def _default_embedder() -> Callable[[Sequence[str]], List[List[float]]]:
    """chromadb's bundled ONNX MiniLM embedding function (downloads once)."""
    from chromadb.utils import embedding_functions

    ef = embedding_functions.DefaultEmbeddingFunction()

    def _embed(texts: Sequence[str]) -> List[List[float]]:
        return [list(map(float, v)) for v in ef(list(texts))]

    return _embed


# ── stores ────────────────────────────────────────────────────────────────────

class NullVectorStore:
    """A do-nothing store used when the feature is disabled or unavailable.

    Mirrors the :class:`VectorStore` surface so callers need no branching, while
    never touching the disk or importing chromadb.
    """

    enabled = False
    backend_name = "null"
    collection_name = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.top_k = 0

    def add_memory(self, text: str, metadata: Optional[Dict[str, Any]] = None,
                   mem_id: Optional[str] = None) -> str:
        return ""

    def search_memories(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        return []

    def count(self) -> int:
        return 0

    def sync_facts(self, facts) -> int:
        return 0

    def sync_summaries(self, summaries) -> int:
        return 0

    def clear_facts(self) -> int:
        return 0

    def migrate_from_json(self, path: Path, kind: str = "fact") -> int:
        return 0


class VectorStore:
    """chromadb-backed semantic memory store (local, on-disk)."""

    enabled = True
    backend_name = "chromadb"

    def __init__(
        self,
        path: Path,
        collection_name: str = "evelyn_memories",
        embedding: str = "default",
        hash_dim: int = 256,
        top_k: int = 4,
        max_distance: Optional[float] = None,
        telemetry: bool = False,
        client: Any = None,
        embedding_function: Optional[Callable[[Sequence[str]], List[List[float]]]] = None,
    ) -> None:
        import chromadb  # lazy: disabled feature must not require the dep

        self.collection_name = collection_name
        self.top_k = max(1, int(top_k))
        self.max_distance = max_distance

        if client is None:
            Path(path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(path),
                settings=chromadb.Settings(anonymized_telemetry=bool(telemetry)),
            )
        self._client = client

        self._embed = embedding_function or self._make_embedder(embedding, hash_dim)
        # We always supply explicit embeddings, so no embedding_function here.
        self._collection = client.get_or_create_collection(name=collection_name)

    @staticmethod
    def _make_embedder(embedding: str, hash_dim: int) -> Callable[[Sequence[str]], List[List[float]]]:
        if str(embedding).lower() == "hash":
            return HashingEmbeddingFunction(dim=hash_dim)
        return _default_embedder()

    # ── write ─────────────────────────────────────────────────────────────────

    def add_memory(self, text: str, metadata: Optional[Dict[str, Any]] = None,
                   mem_id: Optional[str] = None) -> str:
        """Store one memory.  Returns its id, or "" if empty/failed."""
        text = (text or "").strip()
        if not text:
            return ""
        meta = dict(metadata or {})
        if not meta:                      # chromadb rejects empty metadata
            meta = {"kind": "memory"}
        mid = mem_id or stable_id(str(meta.get("kind", "memory")), text)
        try:
            emb = self._embed([text])
            self._collection.upsert(
                ids=[mid], documents=[text], embeddings=emb, metadatas=[meta],
            )
            return mid
        except Exception as e:
            logger.warning("Could not store memory: %s", e)
            return ""

    # ── read ──────────────────────────────────────────────────────────────────

    def count(self) -> int:
        try:
            return int(self._collection.count())
        except Exception:
            return 0

    # ── sync / prune ──────────────────────────────────────────────────────────

    def _snapshot(self):
        """Return aligned ``(ids, documents, metadatas)`` for the whole store."""
        try:
            data = self._collection.get()
        except Exception as e:
            logger.warning("Could not read vector store: %s", e)
            return [], [], []
        ids = list(data.get("ids") or [])
        docs = list(data.get("documents") or [])
        metas = list(data.get("metadatas") or [])
        n = len(ids)
        docs = (docs + [""] * n)[:n]
        metas = (metas + [{}] * n)[:n]
        return ids, docs, metas

    def _delete(self, ids: List[str]) -> int:
        if not ids:
            return 0
        try:
            self._collection.delete(ids=ids)
            return len(ids)
        except Exception as e:
            logger.warning("Could not delete memories: %s", e)
            return 0

    def sync_facts(self, facts: Sequence[str]) -> int:
        """Make ``kind=fact`` vectors mirror the flat fact list.

        Adds missing facts and removes fact vectors no longer in memory.json,
        so forgetting or hand-editing facts cannot be undone by semantic
        recall.  Idempotent: running it again with the same list changes 0.
        """
        wanted: Dict[str, str] = {}
        for fact in facts or []:
            text = str(fact).strip()
            if text:
                wanted[stable_id("fact", text)] = text

        ids, _docs, metas = self._snapshot()
        existing = {i for i, m in zip(ids, metas)
                   if (m or {}).get("kind") == "fact"}
        changed = self._delete([i for i in existing if i not in wanted])

        missing = [(i, text) for i, text in wanted.items()
                   if i not in existing]
        if missing:
            try:
                embeddings = self._embed([text for _, text in missing])
                self._collection.upsert(
                    ids=[i for i, _ in missing],
                    documents=[text for _, text in missing],
                    embeddings=embeddings,
                    metadatas=[{"kind": "fact"} for _ in missing],
                )
                changed += len(missing)
            except Exception as e:
                logger.warning("Could not sync facts: %s", e)
        return changed

    def sync_summaries(self, summaries: Sequence[tuple]) -> int:
        """Mirror current ``(date, summary)`` pairs, one vector per date.

        Ids are ``summary:<date>``, so re-summarising replaces the previous
        vector.  Older content-addressed summaries (the source of duplicate
        recall) and summaries for dates no longer present are deleted.
        """
        wanted: Dict[str, tuple] = {}
        for item in summaries or []:
            try:
                day, text = item
            except (TypeError, ValueError):
                continue
            day = str(day).strip()
            text = str(text or "").strip()
            if day and text:
                wanted[f"summary:{day}"] = (day, f"[{day}] {text}")

        ids, docs, metas = self._snapshot()
        current = {i: doc for i, doc, meta in zip(ids, docs, metas)
                   if (meta or {}).get("kind") == "summary"}
        changed = self._delete([i for i in current if i not in wanted])

        upserts = [(i, day, text) for i, (day, text) in wanted.items()
                   if current.get(i) != text]
        if upserts:
            try:
                embeddings = self._embed([text for _, _, text in upserts])
                self._collection.upsert(
                    ids=[i for i, _, _ in upserts],
                    documents=[text for _, _, text in upserts],
                    embeddings=embeddings,
                    metadatas=[{"kind": "summary", "date": day}
                               for _, day, _ in upserts],
                )
                changed += len(upserts)
            except Exception as e:
                logger.warning("Could not sync summaries: %s", e)
        return changed

    def clear_facts(self) -> int:
        """Delete every fact vector (used by ``/forget``)."""
        ids, _docs, metas = self._snapshot()
        return self._delete([i for i, m in zip(ids, metas)
                             if (m or {}).get("kind") == "fact"])

    def search_memories(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return the ``top_k`` memories most similar to ``query`` (closest first)."""
        query = (query or "").strip()
        if not query or self.count() == 0:
            return []
        k = max(1, int(top_k or self.top_k))
        try:
            emb = self._embed([query])
            res = self._collection.query(query_embeddings=emb, n_results=k)
        except Exception as e:
            logger.debug("Vector search failed: %s", e)
            return []

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]

        hits: List[Dict[str, Any]] = []
        for i, text in enumerate(docs):
            dist = dists[i] if i < len(dists) else None
            if (self.max_distance is not None and dist is not None
                    and dist > float(self.max_distance)):
                continue
            hits.append({
                "id": ids[i] if i < len(ids) else "",
                "text": text,
                "distance": dist,
                "score": None if dist is None else 1.0 / (1.0 + float(dist)),
                "metadata": metas[i] if i < len(metas) else {},
            })
        return hits

    # ── migration ─────────────────────────────────────────────────────────────

    def migrate_from_json(self, path: Path, kind: str = "fact") -> int:
        """Import a flat JSON list of strings as memories; return #newly added.

        Idempotent: deterministic ids + ``upsert`` mean a second run adds 0.
        """
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.info("No memory.json at %s — nothing to migrate", path)
            return 0
        except Exception as e:
            logger.warning("Could not read %s: %s", path, e)
            return 0

        if not isinstance(raw, list):
            logger.warning("Expected a JSON list in %s — skipping", path)
            return 0

        seen, texts = set(), []
        for item in raw:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
        if not texts:
            return 0

        before = self.count()
        ids = [stable_id(kind, t) for t in texts]
        embs = self._embed(texts)
        self._collection.upsert(
            ids=ids, documents=texts, embeddings=embs,
            metadatas=[{"kind": kind} for _ in texts],
        )
        added = max(0, self.count() - before)
        logger.info("Vector memory migration: %d new of %d", added, len(texts))
        return added


# ── factory ───────────────────────────────────────────────────────────────────

def create_vector_store(
    settings: Optional[Dict[str, Any]] = None,
    *,
    path: Optional[Path] = None,
    collection: Optional[str] = None,
    client: Any = None,
    embedding_function: Optional[Callable[[Sequence[str]], List[List[float]]]] = None,
) -> Any:
    """Build the configured store, or a :class:`NullVectorStore`.  Never raises.

    ``client``/``embedding_function`` are injection points for tests (e.g. a
    chromadb ``EphemeralClient`` + :class:`HashingEmbeddingFunction`).
    """
    cfg = load_config()
    cfg.update(settings or {})

    # A real client implies an explicit request (tests); otherwise honour `enabled`.
    if client is None and not cfg.get("enabled"):
        return NullVectorStore()

    try:
        return VectorStore(
            path=path or _resolve_path(cfg.get("path") or DEFAULT_CONFIG["path"]),
            collection_name=collection or str(cfg.get("collection")),
            embedding=str(cfg.get("embedding", "default")),
            hash_dim=int(cfg.get("hash_dim", 256)),
            top_k=int(cfg.get("top_k", 4)),
            max_distance=cfg.get("max_distance"),
            telemetry=bool(cfg.get("telemetry", False)),
            client=client,
            embedding_function=embedding_function,
        )
    except Exception as e:
        logger.warning("Vector memory unavailable (%s) — continuing without it", e)
        return NullVectorStore()


__all__ = [
    "VectorStore",
    "NullVectorStore",
    "HashingEmbeddingFunction",
    "create_vector_store",
    "load_config",
    "stable_id",
    "DEFAULT_CONFIG",
    "DEFAULT_CONFIG_PATH",
]
