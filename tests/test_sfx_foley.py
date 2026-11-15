"""Feature 12c tests: procedural foley generators (tools/gen_sfx_foley.py).

Pure numpy synthesis — durations, levels, determinism, and output naming
that the runtime clip matcher understands.  No files are written into the
real assets folder here (plan tests monkeypatch OUT_DIR).
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

for _m in ("core", "core.sfx"):
    sys.modules.pop(_m, None)

import gen_sfx_foley as foley                       # noqa: E402
from core import sfx as runtime_sfx                 # noqa: E402

RANGES = {          # seconds (min, max) — the cue must feel momentary
    "kiss": (0.18, 0.40),
    "breath": (0.70, 1.40),
    "gasp": (0.35, 0.75),
    "suck": (0.40, 0.80),
    "heartbeat": (0.55, 1.10),
    "sniff": (0.20, 0.55),
    "gulp": (0.30, 0.65),
}


def test_clip_durations_are_sane():
    for stem, (lo, hi) in RANGES.items():
        sig = foley.make_clip(stem, 1)
        dur = sig.size / foley.SR
        assert lo <= dur <= hi, f"{stem}: {dur:.2f}s outside {lo}-{hi}"


def test_levels_are_normalized_and_unclipped():
    for stem in foley.GENERATORS:
        for variant in range(1, foley.VARIANTS + 1):
            sig = foley.make_clip(stem, variant)
            peak = float(np.abs(sig).max())
            assert 0.25 <= peak <= 0.55, f"{stem}_{variant} peak {peak:.2f}"
            assert np.isfinite(sig).all()


def test_generation_is_deterministic():
    a = foley.make_clip("kiss", 1)
    b = foley.make_clip("kiss", 1)
    assert np.array_equal(a, b)
    c = foley.make_clip("kiss", 2)
    assert not np.array_equal(a, c)                 # variants really differ


def test_every_foley_stem_is_mapped_at_runtime():
    # Shipped defaults, not the user's live yaml: the mapping is part of the
    # feature contract, so a live config edit must not break this test.
    cfg = runtime_sfx.load_config(Path("Z:/nope/sfx.yaml"))
    runtime_stems = {s for _, stems in cfg.triggers for s in stems}
    runtime_stems |= {s for _, stems in cfg.words for s in stems}
    assert set(foley.GENERATORS) <= runtime_stems


def test_output_names_resolve_in_the_runtime_matcher():
    for stem in foley.GENERATORS:
        for variant in range(1, foley.VARIANTS + 1):
            name = foley.output_path(stem, variant).name
            assert runtime_sfx.clip_for(stem, [name]) == name


def test_plan_skips_existing_and_force_overwrites(monkeypatch, tmp_path):
    monkeypatch.setattr(foley, "OUT_DIR", tmp_path)
    total = len(foley.GENERATORS) * foley.VARIANTS
    foley.output_path("kiss", 1).write_bytes(b"x")
    rows = foley.plan(force=False)
    assert len(rows) == total - 1
    assert all(p.name != "kiss_1.wav" for _, _, p in rows)
    assert len(foley.plan(force=True)) == total
