"""Import flat facts (config/memory.json) into the vector store.

Idempotent — safe to run any number of times.  Deterministic ids + ``upsert``
mean re-running adds nothing new.

Usage (from the repository root)::

    python src/memory/migrate.py
    python src/memory/migrate.py --json config/memory.json --config config/memory.yaml

or, with ``src`` on the path (from the ``src/`` directory)::

    python -m memory.migrate

The feature must be enabled in config/memory.yaml, or pass ``--force`` to run
against the configured store regardless.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this file directly (python src/memory/migrate.py): put src/ on
# the path so `memory` resolves as a package.
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from memory.vector_store import (                      # noqa: E402
    PROJECT_ROOT, create_vector_store, load_config,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Migrate flat memory facts into the vector store.")
    ap.add_argument("--json", default=str(PROJECT_ROOT / "config" / "memory.json"),
                    help="path to the flat facts JSON (default: config/memory.json)")
    ap.add_argument("--config", default=None,
                    help="path to config/memory.yaml (default: config/memory.yaml)")
    ap.add_argument("--kind", default="fact", help="metadata kind tag (default: fact)")
    ap.add_argument("--force", action="store_true",
                    help="run even if the feature is disabled in the config")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config) if args.config else None)
    if not cfg.get("enabled") and not args.force:
        print("Vector memory is disabled (config/memory.yaml: enabled: false).")
        print("Enable it, or pass --force to migrate anyway.")
        return 1

    # A client is only auto-created when enabled; --force overrides that gate.
    settings = dict(cfg)
    if args.force:
        settings["enabled"] = True

    store = create_vector_store(settings)
    if not getattr(store, "enabled", False):
        print("Vector memory could not be initialised (is chromadb installed?).")
        return 1

    added = store.migrate_from_json(Path(args.json), kind=args.kind)
    print(f"Migrated {added} new memory item(s) from {args.json}.")
    print(f"Store now holds {store.count()} item(s) in '{store.collection_name}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
