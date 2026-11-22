"""Phase 5: SQLite vector store — lightweight replacement for ChromaDB.

This module provides a VectorStore implementation backed by SQLite + NumPy
instead of ChromaDB. Benefits:
- No heavy dependencies (chromadb, onnx, etc.)
- Faster startup (no collection initialization)
- Simpler persistence (single .db file)
- Same interface as vector_store.VectorStore

The embedder is passed in (same HashingEmbeddingFunction or a custom one).
Vectors are stored as BLOBs and queried with NumPy cosine similarity in Python.
This is slower than ChromaDB's HNSW index but sufficient for <1000 memories.

Interface compatibility:
- add_memory() → stores text + embedding + metadata
- search_memories() → cosine similarity search
- count() → total memory count
- sync_facts() → mirror memory.json
- sync_summaries() → mirror summaries
- clear_facts() → delete fact vectors
- migrate_from_json() → import flat list
"""
from __future__ import annotations

import hashlib
import json
import logging
import pickle
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None  # type: ignore

logger = logging.getLogger(__name__)


def stable_id(kind: str, text: str) -> str:
    """Deterministic id for a memory (same as vector_store.py)."""
    digest = hashlib.sha1(f"{kind}\x1f{text}".encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:40]}"


class SQLiteVectorStore:
    """SQLite-backed vector store with NumPy similarity search."""
    
    enabled = True
    backend_name = "sqlite"
    
    def __init__(
        self,
        path: Path,
        collection_name: str = "evelyn_memories",
        embedding_function: Optional[Callable[[Sequence[str]], List[List[float]]]] = None,
        top_k: int = 4,
        max_distance: Optional[float] = None,
    ):
        if not NUMPY_AVAILABLE:
            raise ImportError("NumPy is required for SQLiteVectorStore")
        
        if embedding_function is None:
            raise ValueError("embedding_function is required for SQLiteVectorStore")
        
        self.collection_name = collection_name
        self._embed = embedding_function
        self.top_k = max(1, int(top_k))
        self.max_distance = max_distance
        
        # Create database file
        db_path = Path(path) / f"{collection_name}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    kind TEXT,
                    date TEXT,
                    metadata TEXT
                )
            """)
            # Index on kind for fast filtering
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kind ON memories(kind)
            """)
            # Index on date for summaries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_date ON memories(date)
            """)
            conn.commit()
    
    def _serialize_embedding(self, vec: List[float]) -> bytes:
        """Serialize embedding vector to bytes."""
        return pickle.dumps(np.array(vec, dtype=np.float32))
    
    def _deserialize_embedding(self, blob: bytes) -> np.ndarray:
        """Deserialize embedding vector from bytes."""
        return pickle.loads(blob)
    
    def add_memory(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        mem_id: Optional[str] = None
    ) -> str:
        """Store one memory. Returns its id, or "" if empty/failed."""
        text = (text or "").strip()
        if not text:
            return ""
        
        meta = dict(metadata or {})
        if not meta:
            meta = {"kind": "memory"}
        
        kind = str(meta.get("kind", "memory"))
        date = str(meta.get("date", ""))
        mid = mem_id or stable_id(kind, text)
        
        try:
            # Compute embedding
            emb = self._embed([text])[0]
            emb_blob = self._serialize_embedding(emb)
            
            # Store in database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO memories
                    (id, text, embedding, kind, date, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (mid, text, emb_blob, kind, date, json.dumps(meta)))
                conn.commit()
            
            return mid
        except Exception as e:
            logger.warning("Could not store memory: %s", e)
            return ""
    
    def count(self) -> int:
        """Return total number of memories."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute("SELECT COUNT(*) FROM memories")
                return cur.fetchone()[0]
        except Exception:
            return 0
    
    def search_memories(
        self,
        query: str,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return the top_k memories most similar to query (closest first)."""
        query = (query or "").strip()
        if not query or self.count() == 0:
            return []
        
        k = max(1, int(top_k or self.top_k))
        
        try:
            # Compute query embedding
            query_emb = np.array(self._embed([query])[0], dtype=np.float32)
            
            # Load all memories
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT id, text, embedding, kind, date, metadata
                    FROM memories
                """)
                rows = cur.fetchall()
            
            if not rows:
                return []
            
            # Compute cosine similarities
            similarities: List[Tuple[float, sqlite3.Row]] = []
            for row in rows:
                emb = self._deserialize_embedding(row["embedding"])
                # Cosine similarity = dot product (vectors are L2-normalized)
                similarity = float(np.dot(query_emb, emb))
                # Convert to distance (lower is better)
                distance = 1.0 - similarity
                similarities.append((distance, row))
            
            # Sort by distance (ascending = most similar first)
            similarities.sort(key=lambda x: x[0])
            
            # Apply max_distance filter and top_k limit
            hits: List[Dict[str, Any]] = []
            for dist, row in similarities[:k]:
                if (self.max_distance is not None
                        and dist > float(self.max_distance)):
                    continue
                
                # Parse metadata
                try:
                    meta = json.loads(row["metadata"])
                except Exception:
                    meta = {"kind": row["kind"]}
                
                hits.append({
                    "id": row["id"],
                    "text": row["text"],
                    "distance": dist,
                    "score": 1.0 / (1.0 + dist),
                    "metadata": meta,
                })
            
            return hits
        
        except Exception as e:
            logger.debug("Vector search failed: %s", e)
            return []
    
    def _snapshot(self) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
        """Return (ids, documents, metadatas) for the whole store."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT id, text, metadata FROM memories
                """)
                rows = cur.fetchall()
            
            ids = [row["id"] for row in rows]
            docs = [row["text"] for row in rows]
            metas = []
            for row in rows:
                try:
                    metas.append(json.loads(row["metadata"]))
                except Exception:
                    metas.append({})
            
            return ids, docs, metas
        
        except Exception as e:
            logger.warning("Could not read vector store: %s", e)
            return [], [], []
    
    def _delete(self, ids: List[str]) -> int:
        """Delete memories by id."""
        if not ids:
            return 0
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"""
                    DELETE FROM memories WHERE id IN ({placeholders})
                """, ids)
                conn.commit()
            return len(ids)
        except Exception as e:
            logger.warning("Could not delete memories: %s", e)
            return 0
    
    def sync_facts(self, facts: Sequence[str]) -> int:
        """Make kind=fact vectors mirror the flat fact list."""
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
            for mid, text in missing:
                self.add_memory(text, metadata={"kind": "fact"}, mem_id=mid)
            changed += len(missing)
        
        return changed
    
    def sync_summaries(self, summaries: Sequence[tuple]) -> int:
        """Mirror current (date, summary) pairs, one vector per date."""
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
            for mid, day, text in upserts:
                self.add_memory(
                    text,
                    metadata={"kind": "summary", "date": day},
                    mem_id=mid
                )
            changed += len(upserts)
        
        return changed
    
    def clear_facts(self) -> int:
        """Delete every fact vector (used by /forget)."""
        ids, _docs, metas = self._snapshot()
        return self._delete([i for i, m in zip(ids, metas)
                             if (m or {}).get("kind") == "fact"])
    
    def migrate_from_json(self, path: Path, kind: str = "fact") -> int:
        """Import a flat JSON list of strings as memories."""
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
        for text in texts:
            mid = stable_id(kind, text)
            self.add_memory(text, metadata={"kind": kind}, mem_id=mid)
        
        added = max(0, self.count() - before)
        logger.info("Vector memory migration: %d new of %d", added, len(texts))
        return added


__all__ = ["SQLiteVectorStore", "stable_id"]

