"""Read-only memory doctor for Evelyn.

Compares the sources of truth (``config/memory.json`` and the current daily
summaries) with the vectors stored in ``memory_store``.  It reports missing,
stale, and changed items so you can spot divergence before it becomes a
recall mystery.

Run:
    .venv\\Scripts\\python.exe tools\\memory_doctor.py
    .venv\\Scripts\\python.exe tools\\memory_doctor.py --json
    .venv\\Scripts\\python.exe tools\\memory_doctor.py --root C:\\path\\to\\project

The app's startup sync repairs fact/summary drift; this tool does not write to
the store.  Back up first if you intend to run a restore.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from memory.vector_store import load_config, stable_id    # noqa: E402


def load_facts(path: Path) -> List[str]:
    """Read and deduplicate the flat fact list; malformed input is empty."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def load_summaries(path: Path) -> List[Tuple[str, str]]:
    """Read current ``(date, summary)`` pairs, last write per date wins."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    by_date: Dict[str, str] = {}
    order: List[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "").strip()
        text = str(item.get("summary") or "").strip()
        if not day or not text:
            continue
        if day not in by_date:
            order.append(day)
        by_date[day] = text
    return [(day, by_date[day]) for day in order]


def inspect_collection(collection: Any) -> List[Dict[str, Any]]:
    """Turn a chromadb collection's ``get()`` result into plain items."""
    try:
        data = collection.get()
    except Exception:
        return []
    ids = list(data.get("ids") or [])
    docs = list(data.get("documents") or [])
    metas = list(data.get("metadatas") or [])
    n = len(ids)
    docs = (docs + [""] * n)[:n]
    metas = (metas + [{}] * n)[:n]
    out: List[Dict[str, Any]] = []
    for i, item_id in enumerate(ids):
        out.append({
            "id": str(item_id),
            "document": str(docs[i] or ""),
            "metadata": dict(metas[i] or {}),
        })
    return out


def read_vectors(store_path: Path, collection_name: str) -> List[Dict[str, Any]]:
    """Read the collection without an embedding model or a write."""
    store_path = Path(store_path)
    if not store_path.is_dir():
        return []
    try:
        import chromadb
    except Exception:
        return []
    try:
        client = chromadb.PersistentClient(
            path=str(store_path),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        collection = client.get_collection(collection_name)
    except Exception:
        return []
    return inspect_collection(collection)


def _short(items: Iterable[Any], limit: int = 10) -> List[Any]:
    out = list(items)
    return out[:limit]


def build_report(facts: Sequence[str], summaries: Sequence[Tuple[str, str]],
                 vectors: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare source-of-truth memory with vector-store items.  Pure."""
    wanted_facts = {stable_id("fact", text): text for text in facts}
    wanted_summaries = {
        f"summary:{day}": f"[{day}] {text}" for day, text in summaries
    }
    by_id = {str(item.get("id")): item for item in vectors
             if isinstance(item, dict) and item.get("id")}

    actual_facts = {
        item_id: item for item_id, item in by_id.items()
        if (item.get("metadata") or {}).get("kind") == "fact"
    }
    actual_summaries = {
        item_id: item for item_id, item in by_id.items()
        if (item.get("metadata") or {}).get("kind") == "summary"
    }

    missing_facts = sorted(set(wanted_facts) - set(actual_facts))
    stale_facts = sorted(set(actual_facts) - set(wanted_facts))
    missing_summaries = sorted(set(wanted_summaries) - set(actual_summaries))
    stale_summaries = sorted(set(actual_summaries) - set(wanted_summaries))
    changed_summaries = sorted(
        item_id for item_id, expected in wanted_summaries.items()
        if item_id in actual_summaries
        and str(actual_summaries[item_id].get("document") or "") != expected
    )
    classified = set(actual_facts) | set(actual_summaries)
    other = sorted(set(by_id) - classified)

    return {
        "facts_count": len(facts),
        "facts_unique": len(wanted_facts),
        "summaries_count": len(summaries),
        "vector_count": len(by_id),
        "missing_facts": missing_facts,
        "missing_fact_texts": [wanted_facts[i] for i in missing_facts],
        "stale_facts": stale_facts,
        "stale_fact_texts": [
            str(actual_facts[i].get("document") or "") for i in stale_facts
        ],
        "missing_summaries": missing_summaries,
        "stale_summaries": stale_summaries,
        "changed_summaries": changed_summaries,
        "other_vectors": other,
        "ok": not (missing_facts or stale_facts or missing_summaries
                   or stale_summaries or changed_summaries),
    }


def format_report(report: Dict[str, Any], store_path: Path,
                  collection: str) -> str:
    lines = [
        "Evelyn memory doctor",
        f"  facts: {report['facts_count']} ({report['facts_unique']} unique)",
        f"  summaries: {report['summaries_count']}",
        f"  vector store: {store_path} / {collection}",
        f"  vectors: {report['vector_count']}",
    ]
    if report["ok"]:
        lines.append("OK: facts and summaries match the vector store.")
        return "\n".join(lines)

    lines.append("DRIFT detected:")
    sections = (
        ("missing fact vectors", "missing_fact_texts"),
        ("stale fact vectors", "stale_fact_texts"),
        ("missing summary vectors", "missing_summaries"),
        ("stale summary vectors", "stale_summaries"),
        ("changed summary vectors", "changed_summaries"),
    )
    for label, key in sections:
        values = _short(report.get(key) or [])
        if not values:
            continue
        lines.append(f"  {label} ({len(report[key])}):")
        for value in values:
            lines.append(f"    - {value}")
        if len(report[key]) > len(values):
            lines.append(f"    ... and {len(report[key]) - len(values)} more")
    if report.get("other_vectors"):
        lines.append(
            f"  other vectors kept as-is: {len(report['other_vectors'])}")
        for item in _short(report["other_vectors"]):
            lines.append(f"    - {item}")
    lines.append("Startup sync repairs fact/summary drift; back up first.")
    return "\n".join(lines)


def _resolve_store(root: Path, config_path: Path,
                   explicit_store: str, explicit_collection: str) -> Tuple[Path, str]:
    cfg = load_config(config_path)
    store_raw = explicit_store or str(cfg.get("path") or "memory_store")
    store = Path(store_raw).expanduser()
    if not store.is_absolute():
        store = root / store
    collection = explicit_collection or str(cfg.get("collection") or "evelyn_memories")
    return store, collection


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT),
                    help="project root (default: repository root)")
    ap.add_argument("--store", default="",
                    help="override memory_store path")
    ap.add_argument("--collection", default="",
                    help="override vector collection name")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    config_dir = root / "config"
    store, collection = _resolve_store(
        root, config_dir / "memory.yaml", args.store, args.collection)
    facts = load_facts(config_dir / "memory.json")
    summaries = load_summaries(config_dir / "episodes" / "summaries.json")
    vectors = read_vectors(store, collection)
    report = build_report(facts, summaries, vectors)
    if args.json:
        print(json.dumps({
            "store": str(store),
            "collection": collection,
            **report,
        }, indent=2))
    else:
        print(format_report(report, store, collection))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
