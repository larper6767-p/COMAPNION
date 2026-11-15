# -*- coding: utf-8 -*-
"""Demo: feed the streaming tag parser an awkwardly split reply and print, per
sentence, the cleaned text that would reach TTS plus the attached tags.

Run:
    .venv\\Scripts\\python tools/demo_body_tags.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from body_tags import StreamingTagParser, actions  # noqa: E402

# The reply assembled from the exact split chunks requested.
chunks = [
    "[joy",
    "ful] Oh hi! [fx:bl",
    "ush] You're back. [look:left] Where",
    " were you? [wink:right]",
]

ba = actions()
parser = StreamingTagParser(ba)

print("=== Feeding awkwardly split chunks ===")
print("(reconstructed reply:",
      "".join(repr(c) for c in chunks), ")\n")
all_frames = []
for i, chunk in enumerate(chunks):
    frames = parser.feed(chunk)
    print(f"[chunk {i+1}] {chunk!r} -> emitted {len(frames)} sentence(s)")
    for f in frames:
        all_frames.append(f)

# finish() flushes anything still buffered (the trailing [wink:right]).
final = parser.finish()
all_frames += final

print("\n=== Per-sentence: text that reaches TTS + attached tags ===")
if not all_frames:
    print("(no sentences emitted)")
for n, f in enumerate(all_frames, 1):
    tag_repr = ", ".join(f"{t.kind}:{t.name or ''}" for t in f.tags) or "(none)"
    print(f"{n}. TTS text: {f.text!r}")
    print(f"   mood    : {f.mood}")
    print(f"   tags    : [{tag_repr}]")
