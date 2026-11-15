"""Feature 2 tests: vector-based long-term memory.

Light tests (NullVectorStore, config loading, hashing embeddings, MemoryManager
wiring) run without chromadb.  Integration tests use an in-memory chromadb
EphemeralClient with deterministic hash embeddings — no model download, no
network, no API keys.
"""
import itertools
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# test_headless.py stubs core.* at import time; drop any poisoned entries.
for _m in ("core", "core.config", "core.memory", "memory", "memory.vector_store"):
    sys.modules.pop(_m, None)


def _purge_poisoned():
    """Drop stubs installed by test_headless.py that break chromadb's imports.

    test_headless stubs ``dotenv`` (without ``dotenv_values``), which makes
    chromadb's ``pydantic_settings`` import fail when both files run in one
    session.  Removing the fakes lets the real packages load.
    """
    for name in list(sys.modules):
        if (name == "dotenv" or name.startswith("dotenv.")
                or name == "pydantic_settings" or name.startswith("pydantic_settings.")
                or name == "chromadb" or name.startswith("chromadb.")):
            sys.modules.pop(name, None)


_purge_poisoned()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.vector_store import (                                   # noqa: E402
    VectorStore, NullVectorStore, HashingEmbeddingFunction,
    create_vector_store, load_config, stable_id, DEFAULT_CONFIG,
)

_counter = itertools.count()


@pytest.fixture
def chroma_client():
    _purge_poisoned()            # in case another test file stubbed dotenv first
    chromadb = pytest.importorskip("chromadb")
    return chromadb.EphemeralClient()


@pytest.fixture
def store(chroma_client):
    """A real VectorStore backed by an ephemeral client + deterministic embeddings."""
    return VectorStore(
        path=Path("unused"),
        collection_name=f"t_col_{next(_counter)}",
        client=chroma_client,
        embedding_function=HashingEmbeddingFunction(dim=64),
    )


# ── NullVectorStore ───────────────────────────────────────────────────────────

def test_null_store_is_inert():
    s = NullVectorStore()
    assert s.enabled is False
    assert s.add_memory("anything") == ""
    assert s.search_memories("anything") == []
    assert s.count() == 0
    assert s.sync_facts(["anything"]) == 0
    assert s.sync_summaries([("2026-01-01", "anything")]) == 0
    assert s.clear_facts() == 0
    assert s.migrate_from_json(Path("nope.json")) == 0


# ── factory ───────────────────────────────────────────────────────────────────

def test_factory_disabled_returns_null(tmp_path):
    """The default (disabled) path must not touch chromadb or the disk."""
    s = create_vector_store({"enabled": False, "path": str(tmp_path)})
    assert isinstance(s, NullVectorStore)
    assert s.enabled is False


def test_factory_enabled_with_hash_backend(tmp_path, chroma_client):
    s = create_vector_store(
        {"enabled": True, "embedding": "hash", "hash_dim": 32,
         "collection": f"t_factory_{next(_counter)}", "path": str(tmp_path)},
        client=chroma_client,
    )
    assert s.enabled is True
    assert s.backend_name == "chromadb"


def test_factory_client_implies_enabled(chroma_client):
    """Passing a client (tests) forces a real store even if config says off."""
    s = create_vector_store(
        {"enabled": False, "collection": f"t_force_{next(_counter)}"},
        client=chroma_client,
    )
    assert s.enabled is True


# ── add / count / search ──────────────────────────────────────────────────────

def test_add_and_count(store):
    assert store.count() == 0
    store.add_memory("I love coffee in the morning.")
    store.add_memory("My car is a red sports car.")
    assert store.count() == 2


def test_add_empty_returns_empty(store):
    assert store.add_memory("   ") == ""
    assert store.count() == 0


def test_search_returns_most_relevant_first(store):
    store.add_memory("I love coffee in the morning.", metadata={"kind": "fact"})
    store.add_memory("My car is a red sports car.")
    store.add_memory("The weather is nice today.")
    hits = store.search_memories("coffee", top_k=3)
    assert hits, "expected at least one hit"
    assert "coffee" in hits[0]["text"]
    assert hits[0]["distance"] <= hits[-1]["distance"]     # closest first


def test_search_empty_query_returns_empty(store):
    store.add_memory("something")
    assert store.search_memories("   ") == []


def test_search_empty_store_returns_empty(store):
    assert store.search_memories("anything") == []


def test_max_distance_filter(store):
    for i in range(5):
        store.add_memory(f"memory number {i}")
    store.max_distance = 1.0
    hits = store.search_memories("memory", top_k=5)
    assert all(h["distance"] <= 1.0 for h in hits)


def test_stable_id_is_deterministic_and_kind_scoped():
    assert stable_id("fact", "hello") == stable_id("fact", "hello")
    assert stable_id("fact", "hello") != stable_id("summary", "hello")


# ── migration ─────────────────────────────────────────────────────────────────

def test_migrate_from_json_then_idempotent(tmp_path, store):
    p = tmp_path / "memory.json"
    p.write_text(json.dumps(["fact a", "fact b", "fact a"]), encoding="utf-8")
    assert store.migrate_from_json(p) == 2          # dedupes within the file
    assert store.count() == 2
    assert store.migrate_from_json(p) == 0          # idempotent
    assert store.count() == 2


def test_migrate_missing_file_returns_zero(store):
    assert store.migrate_from_json(Path("does/not/exist.json")) == 0


def test_migrate_non_list_returns_zero(tmp_path, store):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert store.migrate_from_json(p) == 0


# ── sync / prune (Phase 1 polish) ─────────────────────────────────────────────

def test_sync_facts_backfills_and_prunes(store):
    store.add_memory("stale fact", metadata={"kind": "fact"})
    store.add_memory("[2026-01-01] keep this summary",
                     metadata={"kind": "summary", "date": "2026-01-01"})

    assert store.sync_facts(["fact a", "fact b"]) == 3   # delete 1, add 2
    assert store.count() == 3
    assert store.sync_facts(["fact a", "fact b"]) == 0   # idempotent
    assert store.sync_facts(["fact a"]) == 1             # prune fact b
    assert store.count() == 2

    texts = {h["text"] for h in store.search_memories("fact a", top_k=10)}
    assert "fact a" in texts
    assert "stale fact" not in texts
    assert any("summary" in h["text"]
               for h in store.search_memories("keep summary", top_k=10))


def test_sync_summaries_replaces_old_variants(store):
    store.add_memory("[2026-01-01] old one",
                     metadata={"kind": "summary", "date": "2026-01-01"})
    store.add_memory("[2026-01-01] old two",
                     metadata={"kind": "summary", "date": "2026-01-01"})
    store.add_memory("a durable fact", metadata={"kind": "fact"})

    assert store.sync_summaries([("2026-01-01", "new text")]) == 3
    assert store.count() == 2          # one summary + the fact
    assert store.sync_summaries([("2026-01-01", "new text")]) == 0
    assert store.sync_summaries([]) == 1
    assert store.count() == 1          # the fact survives


def test_clear_facts_leaves_summaries(store):
    store.add_memory("a fact", metadata={"kind": "fact"})
    store.add_memory("[2026-01-01] a summary", metadata={"kind": "summary"})
    assert store.clear_facts() == 1
    assert store.count() == 1


# ── hashing embeddings ────────────────────────────────────────────────────────

def test_hashing_embedding_properties():
    ef = HashingEmbeddingFunction(dim=48)
    a = ef(["hello world"])
    b = ef(["hello world"])
    c = ef(["completely different"])
    assert a == b                                   # deterministic
    assert len(a[0]) == 48
    norm = sum(v * v for v in a[0])
    assert abs(norm - 1.0) < 1e-9                   # L2-normalised
    assert a[0] != c[0]
    assert ef.name() == "marin-hash"


# ── config loading ────────────────────────────────────────────────────────────

def test_load_config_missing_file_returns_defaults():
    cfg = load_config(Path("nope/memory.yaml"))
    assert cfg["enabled"] is False
    assert cfg["embedding"] == DEFAULT_CONFIG["embedding"]
    assert cfg["top_k"] == DEFAULT_CONFIG["top_k"]


def test_load_config_reads_yaml_overrides(tmp_path):
    p = tmp_path / "memory.yaml"
    p.write_text(
        "enabled: true\ntop_k: 7\nembedding: hash\nmax_distance: 1.25\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg["enabled"] is True
    assert cfg["top_k"] == 7
    assert cfg["embedding"] == "hash"
    assert cfg["max_distance"] == 1.25


def test_load_config_malformed_yaml_returns_defaults(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("enabled: [unclosed\n", encoding="utf-8")
    assert load_config(p)["enabled"] is False


# ── MemoryManager integration ─────────────────────────────────────────────────

def test_manager_without_store_is_unchanged(tmp_path):
    """Feature off: a query adds nothing and the block matches the legacy form."""
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.add_fact("user likes tea")
    block = m.build_context_block(max_facts=20, query="do I like tea?")
    assert "Relevant context from past conversations" not in block
    assert "Things you know about the user: user likes tea." in block


def test_manager_embeds_new_facts(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    m.add_fact("user loves coffee")
    m.add_fact("user loves coffee")          # dedup: the flat list ignores it
    assert store.count() == 1


def test_manager_injects_relevant_context(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    # A summary is stored (facts also live in the flat list, which is capped out
    # of this block so the relevant section is what surfaces it).
    store.add_memory("[2026-01-01] We discussed her love of coffee.", metadata={"kind": "summary"})
    block = m.build_context_block(query="What does she like?")
    assert "Relevant context from past conversations" in block
    assert "coffee" in block


def test_manager_skips_memories_already_in_fact_line(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    m.add_fact("user loves coffee")
    # The only memory equals the (already injected) fact -> no duplicate section.
    block = m.build_context_block(max_facts=20, query="coffee")
    assert "Relevant context from past conversations" not in block


def test_manager_save_summary_embeds(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    m.save_summary("2026-02-02", "A quiet day; we talked about music.")
    assert store.count() == 1


def test_manager_save_summary_replaces_same_day(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    m.save_summary("2026-02-02", "first version")
    m.save_summary("2026-02-02", "second version")
    assert store.count() == 1
    hits = store.search_memories("second version", top_k=5)
    assert hits and "second version" in hits[0]["text"]


def test_manager_sync_vector_store_mirrors_sources(tmp_path, store):
    from core.memory import MemoryManager
    m = MemoryManager(tmp_path, tiers={})
    m.attach_vector_store(store)
    m.load_facts(["user likes tea", "user likes coffee"])
    m.episode_store.save_summary("2026-02-02", "we talked about music")

    assert m.sync_vector_store() == 3     # backfill 2 facts + 1 summary
    assert store.count() == 3
    assert m.sync_vector_store() == 0     # idempotent

    m.load_facts(["user likes tea"])
    assert m.sync_vector_store() == 1     # prune the coffee fact
    assert store.count() == 2

    m.clear_facts()
    assert store.count() == 1             # summary survives /forget


def test_companion_syncs_vector_store_at_startup():
    src = (Path(__file__).parent.parent / "src" / "she.py").read_text(
        encoding="utf-8")
    assert "sync_vector_store()" in src
