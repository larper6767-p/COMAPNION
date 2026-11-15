"""Memory-doctor tests (tools/memory_doctor.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from memory.vector_store import stable_id                    # noqa: E402
from memory_doctor import (                                   # noqa: E402
    build_report, format_report, inspect_collection, load_facts,
    load_summaries,
)


def _fact(text):
    return {"id": stable_id("fact", text), "document": text,
            "metadata": {"kind": "fact"}}


def _summary(day, text):
    return {"id": f"summary:{day}", "document": f"[{day}] {text}",
            "metadata": {"kind": "summary", "date": day}}


def test_report_ok_when_vectors_match_sources():
    facts = ["The user likes tea.", "The user lives in Guwahati."]
    summaries = [("2026-01-01", "A quiet day.")]
    vectors = [_fact(text) for text in facts] + [_summary(*summaries[0])]
    report = build_report(facts, summaries, vectors)
    assert report["ok"] is True
    assert report["facts_count"] == 2
    assert report["vector_count"] == 3


def test_report_detects_missing_stale_and_changed_items():
    facts = ["keep", "missing"]
    summaries = [("2026-01-02", "new text")]
    vectors = [
        _fact("keep"),
        _fact("stale-fact"),
        _summary("2026-01-01", "old summary"),
        _summary("2026-01-02", "different text"),
        {"id": "other-1", "document": "other", "metadata": {"kind": "memory"}},
    ]
    report = build_report(facts, summaries, vectors)
    assert report["ok"] is False
    assert report["missing_facts"] == [stable_id("fact", "missing")]
    assert report["missing_fact_texts"] == ["missing"]
    assert report["stale_fact_texts"] == ["stale-fact"]
    assert report["missing_summaries"] == []
    assert report["stale_summaries"] == ["summary:2026-01-01"]
    assert report["changed_summaries"] == ["summary:2026-01-02"]
    assert report["other_vectors"] == ["other-1"]


def test_report_missing_summary():
    report = build_report([], [("2026-01-03", "text")], [])
    assert report["missing_summaries"] == ["summary:2026-01-03"]
    assert report["ok"] is False


def test_load_facts_dedupes_and_ignores_bad_shape(tmp_path):
    p = tmp_path / "memory.json"
    p.write_text('["A", " A ", "", 3, "B"]', encoding="utf-8")
    assert load_facts(p) == ["A", "3", "B"]
    p.write_text('{"not": "a list"}', encoding="utf-8")
    assert load_facts(p) == []
    assert load_facts(tmp_path / "missing.json") == []


def test_load_summaries_last_for_date_wins(tmp_path):
    p = tmp_path / "summaries.json"
    p.write_text(
        '[{"date": "2026-01-01", "summary": "old"},'
        ' {"date": "2026-01-01", "summary": "new"},'
        ' {"date": "", "summary": "ignored"}]',
        encoding="utf-8",
    )
    assert load_summaries(p) == [("2026-01-01", "new")]


def test_inspect_collection_reads_aligned_rows():
    class FakeCollection:
        def get(self):
            return {
                "ids": ["a", "b"],
                "documents": ["doc-a"],
                "metadatas": [{"kind": "fact"}],
            }

    items = inspect_collection(FakeCollection())
    assert items == [
        {"id": "a", "document": "doc-a", "metadata": {"kind": "fact"}},
        {"id": "b", "document": "", "metadata": {}},
    ]


def test_format_report_lists_drift():
    report = build_report(
        ["missing"], [], [_fact("stale")])
    text = format_report(report, Path("store"), "evelyn_memories")
    assert "DRIFT detected" in text
    assert "missing" in text
    assert "stale" in text
    assert "Startup sync repairs" in text
