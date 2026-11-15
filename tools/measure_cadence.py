"""Feature 30 (Tier 1 #3): measure the aliveness layer's effect on cadence.

Before the aliveness layer (Feature 27), 58% of her assistant messages
contained a question and 52% ENDED with one - the "interview cadence" traced
to the persona's End on a hook instruction.  This tool reads the episode logs
(config/episodes/<date>.json) and prints per-day cadence stats so a before /
after comparison is one command.  Silent **narration** and private
{{remember: ...}} notes are stripped before counting questions, and the
beats/notes columns keep the quality follow-ups measurable.

Run:
    .venv\\Scripts\\python.exe tools\\measure_cadence.py            # all days
    .venv\\Scripts\\python.exe tools\\measure_cadence.py --days 3   # last 3
    .venv\\Scripts\\python.exe tools\\measure_cadence.py --json     # machine output

Pure core functions (load_days / analyze) are importable for tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parent.parent
EPISODE_DIR = ROOT / "config" / "episodes"

# The cadence tool runs from tools/, so make the pure core helpers importable.
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_QUESTION_END = re.compile(r"\?\s*[\"')\]\u2019\u201d]*\s*$")


def _spoken_text(text: str) -> str:
    """Voice-only text: drop silent narration and private memory notes."""
    if not isinstance(text, str):
        return ""
    try:
        from core.narration import strip_narration
        from core.memory_notes import strip_notes
        return strip_narration(strip_notes(text))
    except Exception:
        # Keep the tool usable even if the core package is unavailable.
        return text.strip()


def _narration_spans(text: str) -> List[str]:
    if not isinstance(text, str):
        return []
    try:
        from core.narration import extract_narration
        return extract_narration(text)
    except Exception:
        return re.findall(r"\*\*(.+?)\*\*", text)


def _memory_notes(text: str) -> List[str]:
    if not isinstance(text, str):
        return []
    try:
        from core.memory_notes import extract_notes
        return extract_notes(text)
    except Exception:
        return re.findall(
            r"\{\{\s*remember\s*:\s*(.+?)\s*\}\}", text, re.IGNORECASE)


def analyze(messages: List[str]) -> Dict[str, Any]:
    """Cadence stats for one day's assistant texts.  Pure.

    Question metrics ignore display-only ``**narration**`` and
    ``{{remember: ...}}`` notes, matching what was actually spoken.  The
    narration/note counters make the quality follow-ups measurable too.
    """
    n = len(messages)
    if n == 0:
        return {"count": 0, "ends_with_question": 0, "ends_with_question_pct": 0.0,
                "contains_question": 0, "contains_question_pct": 0.0,
                "question_pairs": 0, "avg_chars": 0,
                "narration_msgs": 0, "memory_note_msgs": 0,
                "one_word_narration_spans": 0}
    spoken = [_spoken_text(m) for m in messages]
    ends = sum(1 for m in spoken if _QUESTION_END.search(m.strip()))
    contains = sum(1 for m in spoken if "?" in m)
    pairs = sum(
        1 for a, b in zip(spoken, spoken[1:])
        if _QUESTION_END.search(a.strip()) and _QUESTION_END.search(b.strip())
    )
    avg = round(sum(len(m) for m in spoken) / n)
    narration = [_narration_spans(m) for m in messages]
    notes = [_memory_notes(m) for m in messages]
    return {
        "count": n,
        "ends_with_question": ends,
        "ends_with_question_pct": round(100.0 * ends / n, 1),
        "contains_question": contains,
        "contains_question_pct": round(100.0 * contains / n, 1),
        "question_pairs": pairs,
        "avg_chars": avg,
        "narration_msgs": sum(1 for spans in narration if spans),
        "memory_note_msgs": sum(1 for found in notes if found),
        "one_word_narration_spans": sum(
            1 for spans in narration for span in spans
            if len(span.split()) == 1),
    }


def load_days(episodes_dir: Path = EPISODE_DIR) -> Dict[str, Dict[str, Any]]:
    """Analyze every day's episode file.  Keys are dates (sorted)."""
    days: Dict[str, Dict[str, Any]] = {}
    if not episodes_dir.is_dir():
        return days
    for path in sorted(episodes_dir.glob("????-??-??.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        texts = [e.get("text", "") for e in entries
                 if isinstance(e, dict) and e.get("role") == "assistant"]
        if texts:
            days[path.stem] = analyze(texts)
    return days


def summarize(days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """All-day totals (messages pooled, weighted by count)."""
    n = sum(d["count"] for d in days.values())
    if not n:
        return {"count": 0, "ends_with_question_pct": 0.0,
                "contains_question_pct": 0.0, "avg_chars": 0,
                "narration_msgs": 0, "memory_note_msgs": 0,
                "one_word_narration_spans": 0}
    ends = sum(round(d["ends_with_question_pct"] / 100.0 * d["count"])
               for d in days.values())
    contains = sum(round(d["contains_question_pct"] / 100.0 * d["count"])
                   for d in days.values())
    return {
        "count": n,
        "ends_with_question_pct": round(100.0 * ends / n, 1),
        "contains_question_pct": round(100.0 * contains / n, 1),
        "avg_chars": round(sum(d["avg_chars"] * d["count"]
                               for d in days.values()) / n),
        "narration_msgs": sum(int(d.get("narration_msgs", 0))
                              for d in days.values()),
        "memory_note_msgs": sum(int(d.get("memory_note_msgs", 0))
                                for d in days.values()),
        "one_word_narration_spans": sum(
            int(d.get("one_word_narration_spans", 0))
            for d in days.values()),
    }


def print_table(days: Dict[str, Dict[str, Any]]) -> None:
    total = summarize(days)
    print(f"{'date':<12} {'msgs':>5} {'ends %':>8} {'contains %':>11} "
          f"{'avg':>5} {'beats':>6} {'notes':>6}")
    for date, d in days.items():
        print(f"{date:<12} {d['count']:>5} {d['ends_with_question_pct']:>7.1f}% "
              f"{d['contains_question_pct']:>10.1f}% {d['avg_chars']:>5} "
              f"{d.get('narration_msgs', 0):>6} {d.get('memory_note_msgs', 0):>6}")
    if total["count"]:
        print(f"{'TOTAL':<12} {total['count']:>5} "
              f"{total['ends_with_question_pct']:>7.1f}% "
              f"{total['contains_question_pct']:>10.1f}% "
              f"{total['avg_chars']:>5} {total['narration_msgs']:>6} "
              f"{total['memory_note_msgs']:>6}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None,
                    help="only the last N days")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    args = ap.parse_args()

    days = load_days()
    if args.days is not None:
        if args.days <= 0:
            days = {}
        else:
            dates = sorted(days)[-args.days:]
            days = {d: days[d] for d in dates}
    if args.json:
        print(json.dumps({"days": days, "total": summarize(days)}, indent=2))
    else:
        if not days:
            print(f"no episode data found in {EPISODE_DIR}")
            return 1
        print_table(days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
