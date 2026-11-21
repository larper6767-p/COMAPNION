"""Phase 1 tests: prompt-log parsing and aggregate reporting.

Pure functions only; no live app or network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from measure_prompt import (  # noqa: E402
    analyze, find_logs, parse_lines, summary_text,
)

PLAN = (
    "[prompt] total=6341 budget=12000 pct=53 sys=4521 hist=1820 "
    "msgs=30/30 all=40 trimmed=4 image=no "
    "sections=persona:3643,aliveness:350,memory:528"
)
USAGE = (
    "[prompt] usage prompt=6120 completion=87 finish=stop est=6341 ratio=0.97"
)


def test_parse_plan_and_usage_lines():
    records = parse_lines("\n".join([
        "noise line",
        PLAN,
        USAGE,
        "[prompt] something else",
    ]))
    assert [r["kind"] for r in records] == ["plan", "usage"]
    plan, usage = records
    assert plan["total"] == 6341
    assert plan["budget"] == 12000
    assert plan["sys"] == 4521
    assert plan["hist"] == 1820
    assert plan["trimmed"] == 4
    assert plan["image"] is False
    assert usage["prompt"] == 6120
    assert usage["completion"] == 87
    assert usage["finish"] == "stop"
    assert usage["ratio"] == 0.97


def test_parse_observe_only_and_flags():
    records = parse_lines(
        "[prompt] total=100 budget=off sys=80 hist=20 msgs=2/2 "
        "all=2 trimmed=0 image=yes sections=persona:80 over-budget")
    assert records[0]["budget"] == 0
    assert records[0]["image"] is True
    assert records[0]["over"] is True


def test_analyze_aggregates_turns_and_usage():
    stats = analyze(parse_lines("\n".join([PLAN, USAGE])))
    assert stats["turns"] == 1
    assert stats["usage_reports"] == 1
    assert stats["avg_estimated_prompt"] == 6341.0
    assert stats["max_estimated_prompt"] == 6341
    assert stats["avg_system"] == 4521.0
    assert stats["avg_history"] == 1820.0
    assert stats["trimmed_turns"] == 1
    assert stats["avg_actual_prompt"] == 6120.0
    assert stats["avg_completion"] == 87.0
    assert stats["avg_estimate_ratio"] == 0.97
    assert stats["finish_reasons"] == {"stop": 1}


def test_analyze_empty_is_safe():
    stats = analyze([])
    assert stats["turns"] == 0
    assert stats["usage_reports"] == 0
    assert stats["avg_estimated_prompt"] == 0.0
    assert stats["finish_reasons"] == {}


def test_summary_text_is_human_readable():
    text = summary_text(analyze(parse_lines("\n".join([PLAN, USAGE]))))
    assert "Turns measured: 1" in text
    assert "Estimated prompt" in text
    assert "Provider usage reports: 1" in text
    assert "stop" in text


def test_find_logs_includes_rotated_files(tmp_path):
    first = tmp_path / "evelyn.log.1"
    second = tmp_path / "evelyn.log"
    first.write_text("old", encoding="utf-8")
    second.write_text("new", encoding="utf-8")
    paths = find_logs(tmp_path)
    assert set(paths) == {first, second}
    assert (tmp_path / "notes.txt") not in paths
