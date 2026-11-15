"""Vector-based long-term memory (Feature 2)."""
from memory.vector_store import (          # noqa: F401
    VectorStore,
    NullVectorStore,
    HashingEmbeddingFunction,
    create_vector_store,
    load_config,
    stable_id,
)

__all__ = [
    "VectorStore",
    "NullVectorStore",
    "HashingEmbeddingFunction",
    "create_vector_store",
    "load_config",
    "stable_id",
]
