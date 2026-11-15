"""Tests for tools/gen_sfx.py (Feature 12b) — pure parts, no network.

The generator synthesises her SFX clips with her own Fish voice; these
tests lock the pure logic: silence trimming, output naming that matches
the runtime clip matcher, the stem table staying in sync with
config/sfx.yaml, and skip-existing behaviour.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

for _m in ("core", "core.sfx"):
    sys.modules.pop(_m, None)

import gen_sfx                                      # noqa: E402
import gen_sfx_foley                                # noqa: E402
from core import sfx as runtime_sfx                 # noqa: E402


def test_trim_bounds_cuts_surrounding_silence():
    mono = np.zeros(4410, dtype=np.float32)
    mono[1000:2000] = 0.5
    start, end = gen_sfx.trim_bounds(mono, threshold=0.01, pad=0)
    assert start == 1000 and end == 2000


def test_trim_bounds_pads_and_clamps():
    mono = np.zeros(500, dtype=np.float32)
    mono[10:20] = 0.5
    start, end = gen_sfx.trim_bounds(mono, threshold=0.01, pad=100)
    assert (start, end) == (0, 120)                 # padded + clamped to len


def test_trim_bounds_all_silent_keeps_everything():
    mono = np.zeros(300, dtype=np.float32)
    assert gen_sfx.trim_bounds(mono) == (0, 300)
    assert gen_sfx.trim_bounds(np.zeros(0, dtype=np.float32)) == (0, 0)


def test_every_generated_stem_is_mapped_at_runtime():
    # Shipped defaults, not the user's live yaml: the mapping is part of the
    # feature contract, so a live config edit must not break this test.
    cfg = runtime_sfx.load_config(Path("Z:/nope/sfx.yaml"))
    runtime_stems = {s for _, stems in cfg.triggers for s in stems}
    runtime_stems |= {s for _, stems in cfg.words for s in stems}
    assert set(gen_sfx.PROMPTS) <= runtime_stems, (
        "generator stems missing from config/sfx.yaml: "
        f"{set(gen_sfx.PROMPTS) - runtime_stems}")


def test_voice_and_foley_stems_never_overlap():
    """A stem is either generated from speech or synthesised as foley —
    never both, so --force in one tool can't corrupt the other's clips."""
    assert not (set(gen_sfx.PROMPTS) & set(gen_sfx_foley.GENERATORS))


def test_output_names_resolve_in_the_runtime_matcher():
    for stem in gen_sfx.PROMPTS:
        name = gen_sfx.output_path(stem, 1).name
        assert runtime_sfx.clip_for(stem, [name]) == name
    # sanity: no accidental prefix collisions
    assert runtime_sfx.clip_for("heart", ["hearty_1.wav"]) is None


def test_plan_skips_existing_clips(monkeypatch, tmp_path):
    monkeypatch.setattr(gen_sfx, "OUT_DIR", tmp_path)
    monkeypatch.setattr(gen_sfx, "SPEED", gen_sfx.SPEED)  # untouched
    total = sum(len(v) for v in gen_sfx.PROMPTS.values())
    gen_sfx.output_path("heart", 1).write_bytes(b"x")
    rows = gen_sfx.plan(force=False)
    assert len(rows) == total - 1
    assert all(path.name != "heart_1.wav" for _, _, _, path in rows)
    assert len(gen_sfx.plan(force=True)) == total
