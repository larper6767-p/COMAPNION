"""Phase 1: summarize prompt-size and provider-usage lines from Evelyn logs.

The app writes two privacy-safe lines per measured turn:

    [prompt] total=6341 budget=12000 pct=53 sys=4521 hist=1820 ...
    [prompt] usage prompt=6120 completion=87 finish=stop est=6341 ratio=0.97

No prompt text is recorded.  This tool reads current and rotated logs and
prints aggregate size/utilization numbers so a budget or model change can be
compared before/after.

Run:
    .venv\\Scripts\\python.exe tools\\measure_prompt.py
    .venv\\Scripts\\python.exe tools\\measure_prompt.py --days 7
    .venv\\Scripts\\python.exe tools\\measure_prompt.py --since "2026-11-21 22:00:00"
    .venv\\Scripts\\python.exe tools\\measure_prompt.py --json

Pure functions (parse_lines / analyze) are importable for tests.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"

_FIELD_RE = re.compile(r"([A-Za-z_]+)=([^\s]+)")
_PLAN_MARK = "[prompt] total="
_USAGE_MARK = "[prompt] usage "
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def parse_lines(text: str,
                since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Extract prompt reports from raw log text; ignore everything else.

    ``since`` is an optional ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS`` lower
    bound applied to the leading timestamp of each log line.
    """
    records: List[Dict[str, Any]] = []
    cutoff = (since or "").strip()
    for line in str(text or "").splitlines():
        if cutoff:
            match = _LOG_TS_RE.match(line)
            if match and match.group(1) < cutoff:
                continue
        if _PLAN_MARK in line:
            fields = dict(_FIELD_RE.findall(line))
            if "total" not in fields:
                continue
            budget_raw = fields.get("budget", "off")
            records.append({
                "kind": "plan",
                "total": _int(fields.get("total")),
                "budget": _int(budget_raw) if budget_raw != "off" else 0,
                "sys": _int(fields.get("sys")),
                "hist": _int(fields.get("hist")),
                "msgs": str(fields.get("msgs", "")),
                "trimmed": _int(fields.get("trimmed")),
                "image": fields.get("image", "no") == "yes",
                "over": "over-budget" in line,
                "near": "near-budget" in line,
            })
        elif _USAGE_MARK in line:
            fields = dict(_FIELD_RE.findall(line))
            if "prompt" not in fields:
                continue
            records.append({
                "kind": "usage",
                "prompt": _int(fields.get("prompt")),
                "completion": _int(fields.get("completion")),
                "finish": fields.get("finish", ""),
                "est": _int(fields.get("est")),
                "ratio": _float(fields.get("ratio")),
            })
    return records


def _mean(values: Iterable[float]) -> float:
    items = [float(v) for v in values]
    return sum(items) / len(items) if items else 0.0


def analyze(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate parsed plan/usage records into one JSON-safe dict."""
    plans = [r for r in records if r.get("kind") == "plan"]
    usages = [r for r in records if r.get("kind") == "usage"]
    budgeted = [p for p in plans if int(p.get("budget") or 0) > 0]
    utilizations = [
        p["total"] / p["budget"] for p in budgeted if p.get("budget")
    ]
    finishes: Dict[str, int] = {}
    for usage in usages:
        finish = str(usage.get("finish") or "?")
        finishes[finish] = finishes.get(finish, 0) + 1
    return {
        "turns": len(plans),
        "usage_reports": len(usages),
        "avg_estimated_prompt": round(
            _mean(p.get("total", 0) for p in plans), 1),
        "max_estimated_prompt": max(
            (int(p.get("total") or 0) for p in plans), default=0),
        "min_estimated_prompt": min(
            (int(p.get("total") or 0) for p in plans), default=0),
        "avg_system": round(_mean(p.get("sys", 0) for p in plans), 1),
        "avg_history": round(_mean(p.get("hist", 0) for p in plans), 1),
        "trimmed_turns": sum(1 for p in plans if int(p.get("trimmed") or 0) > 0),
        "over_budget_turns": sum(1 for p in plans if p.get("over")),
        "near_budget_turns": sum(1 for p in plans if p.get("near")),
        "image_turns": sum(1 for p in plans if p.get("image")),
        "avg_budget_utilization": round(_mean(utilizations), 3),
        "avg_actual_prompt": round(
            _mean(u.get("prompt", 0) for u in usages), 1),
        "avg_completion": round(
            _mean(u.get("completion", 0) for u in usages), 1),
        "avg_estimate_ratio": round(
            _mean(u.get("ratio", 0) for u in usages
                  if float(u.get("ratio") or 0) > 0), 3),
        "finish_reasons": finishes,
    }


def find_logs(log_dir: Path = LOG_DIR,
              days: Optional[float] = None) -> List[Path]:
    """Return current + rotated evelyn logs, oldest first (optional age cap)."""
    paths = sorted(
        (p for p in Path(log_dir).glob("evelyn.log*") if p.is_file()),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
    )
    if days is not None and days > 0:
        import time
        cutoff = time.time() - float(days) * 86400
        paths = [p for p in paths if p.stat().st_mtime >= cutoff]
    return paths


def read_logs(paths: Iterable[Path]) -> str:
    chunks: List[str] = []
    for path in paths:
        try:
            chunks.append(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def summary_text(stats: Dict[str, Any]) -> str:
    lines = [
        f"Turns measured: {stats['turns']}",
        f"Estimated prompt: avg={stats['avg_estimated_prompt']:.0f} "
        f"min={stats['min_estimated_prompt']} "
        f"max={stats['max_estimated_prompt']}",
        f"  system avg={stats['avg_system']:.0f}  "
        f"history avg={stats['avg_history']:.0f}",
        f"  trimmed turns={stats['trimmed_turns']}  "
        f"near budget={stats['near_budget_turns']}  "
        f"over budget={stats['over_budget_turns']}  "
        f"image turns={stats['image_turns']}",
        f"  avg budget utilization={stats['avg_budget_utilization']:.0%}",
        f"Provider usage reports: {stats['usage_reports']}",
        f"  prompt avg={stats['avg_actual_prompt']:.0f}  "
        f"completion avg={stats['avg_completion']:.0f}  "
        f"estimate ratio={stats['avg_estimate_ratio']:.2f}",
        f"  finish reasons: {stats['finish_reasons']}",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize [prompt] size/usage lines from Evelyn logs.")
    parser.add_argument("--log", action="append", default=[],
                        help="Log file to read (repeatable). Default: logs/evelyn.log*")
    parser.add_argument("--log-dir", default=str(LOG_DIR),
                        help="Log directory (default: logs)")
    parser.add_argument("--days", type=float, default=None,
                        help="Only include logs modified in the last N days")
    parser.add_argument("--since", default=None,
                        help="Only include log lines at or after this "
                             "YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' timestamp")
    parser.add_argument("--json", action="store_true",
                        help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    paths = ([Path(p) for p in args.log] if args.log
             else find_logs(Path(args.log_dir), days=args.days))
    if not paths:
        print("No Evelyn logs found.", file=sys.stderr)
        return 1
    records = parse_lines(read_logs(paths), since=args.since)
    stats = analyze(records)
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        print(summary_text(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
