"""Feature 12c: procedural foley for the NON-VOCAL SFX stems.

Her TTS can only articulate words — asking Fish for "mwah" yields a spoken
word, never a lip-smack.  Kisses, breaths, gasps, slurps, heartbeats,
sniffs and gulps are noise transients, so this tool synthesises them
locally with numpy (no network, no downloaded assets, royalty-free by
construction) and writes:

    assets/sfx/kiss_1.wav    kiss_2.wav    kiss_3.wav
    assets/sfx/breath_1.wav  breath_2.wav  breath_3.wav
    assets/sfx/gasp_1.wav    gasp_2.wav    gasp_3.wav
    assets/sfx/suck_1.wav    suck_2.wav    suck_3.wav
    assets/sfx/heartbeat_1.wav ... sniff_*.wav ... gulp_*.wav

These REPLACE the voice-generated files for those stems (same names).
Voice-generated stems from tools/gen_sfx.py stay as they are for the cues
that really are vocalisations: heart / tender / giggle / moan / whimper /
sigh / laugh / purr.

    .venv\\Scripts\\python.exe tools\\gen_sfx_foley.py
    .venv\\Scripts\\python.exe tools\\gen_sfx_foley.py --dry-run
    .venv\\Scripts\\python.exe tools\\gen_sfx_foley.py --only kiss --force
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "sfx"

SR = 44100
PEAK = 0.5              # subtle: these sit UNDER her voice (config volume too)
VARIANTS = 3            # Feature 12d: three takes per stem = less repetition


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _band(noise: np.ndarray, lo: float, hi: float,
          sr: int = SR) -> np.ndarray:
    """FFT band-pass with softly-smoothed edges (keeps transients clean)."""
    n = noise.size
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    mask = ((freqs >= lo) & (freqs <= hi)).astype(np.float64)
    kernel = np.hanning(9)
    kernel /= kernel.sum()
    mask = np.convolve(mask, kernel, mode="same")
    out = np.fft.irfft(spec * mask, n)
    return out


def _normalize(sig: np.ndarray, peak: float = PEAK) -> np.ndarray:
    m = float(np.abs(sig).max()) if sig.size else 0.0
    if m <= 0.0:
        return sig
    return (sig / m) * peak


def _noise(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.standard_normal(n)


def make_kiss(seed: int = 1) -> np.ndarray:
    """Two quick transients: the press, then the release 'smack' + thump."""
    rng = _rng(seed)
    n = int(0.25 * SR)
    body = _band(_noise(rng, n), 800.0, 4200.0)

    env = np.zeros(n)
    for start, length, decay in ((0.015, 0.05, 0.018), (0.09, 0.14, 0.045)):
        i0 = int(start * SR)
        i1 = min(n, i0 + int(length * SR))
        t = np.arange(i1 - i0) / SR
        env[i0:i1] += np.exp(-t / decay)

    sig = body * env
    # low body thump under the release
    i0 = int(0.09 * SR)
    tn = np.arange(min(int(0.06 * SR), n - i0)) / SR
    thump = 0.55 * np.sin(2 * np.pi * 135.0 * tn) * np.exp(-tn / 0.02)
    sig[i0:i0 + thump.size] += thump
    return _normalize(sig)


def make_breath(seed: int = 1) -> np.ndarray:
    """One soft exhale: low hiss with a slow fall and a little wobble."""
    rng = _rng(seed)
    n = int(1.0 * SR)
    sig = _band(_noise(rng, n), 250.0, 1400.0)
    t = np.arange(n) / SR
    env = np.clip(t / 0.14, 0.0, 1.0) * np.exp(-np.maximum(t - 0.14, 0) / 0.45)
    env *= 1.0 + 0.15 * np.sin(2 * np.pi * 3.0 * t + rng.uniform(0, 6.28))
    return _normalize(sig * env)


def make_gasp(seed: int = 1) -> np.ndarray:
    """A quick inhale: brightening noise that swells then cuts."""
    rng = _rng(seed)
    n = int(0.5 * SR)
    sig = _band(_noise(rng, n), 500.0, 2800.0)
    t = np.arange(n) / SR
    env = np.clip(t / 0.16, 0.0, 1.0) ** 1.6
    env *= np.exp(-np.maximum(t - 0.16, 0) / 0.10)
    return _normalize(sig * env)


def make_suck(seed: int = 1) -> np.ndarray:
    """A short wet slurp: narrow band with an uneven, wobbling draw."""
    rng = _rng(seed)
    n = int(0.55 * SR)
    sig = _band(_noise(rng, n), 300.0, 1300.0)
    t = np.arange(n) / SR
    env = np.clip(t / 0.10, 0.0, 1.0) * np.exp(-np.maximum(t - 0.10, 0) / 0.28)
    env *= 0.7 + 0.3 * np.sin(2 * np.pi * 6.5 * t + rng.uniform(0, 6.28))
    return _normalize(sig * env)


def make_heartbeat(seed: int = 1) -> np.ndarray:
    """A soft 'ba-dum': two low thumps, then a fainter pair."""
    rng = _rng(seed)
    n = int(0.85 * SR)
    sig = np.zeros(n)
    for start, gain, freq in ((0.00, 1.00, 58.0),
                              (0.17, 0.72, 70.0),
                              (0.44, 0.85, 58.0),
                              (0.61, 0.60, 70.0)):
        i0 = int(start * SR)
        ln = min(int(0.16 * SR), n - i0)
        t = np.arange(ln) / SR
        phase = 2 * np.pi * (freq * t - 16.0 * t * t)   # gentle downward sweep
        thump = np.sin(phase) * np.exp(-t / 0.035)
        click = _band(_noise(rng, ln), 300.0, 1600.0) * np.exp(-t / 0.008)
        sig[i0:i0 + ln] += gain * (thump + 0.20 * click)
    return _normalize(sig)


def make_sniff(seed: int = 1) -> np.ndarray:
    """A short double sniff: two quick inhaled noise swells."""
    rng = _rng(seed)
    n = int(0.35 * SR)
    sig = np.zeros(n)
    for start, dur, gain in ((0.00, 0.075, 1.0), (0.13, 0.11, 0.8)):
        i0 = int(start * SR)
        ln = min(int(dur * SR), n - i0)
        noise = _band(_noise(rng, ln), 600.0, 3400.0)
        t = np.arange(ln) / SR
        env = np.clip(t / dur, 0.0, 1.0) ** 0.8
        sig[i0:i0 + ln] += gain * noise * env
    return _normalize(sig)


def make_gulp(seed: int = 1) -> np.ndarray:
    """A wet swallow: a fast low 'glk' plus a second, smaller one."""
    rng = _rng(seed)
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    phase = 2 * np.pi * (150.0 * t - 120.0 * t * t)
    env = np.clip(t / 0.012, 0.0, 1.0) * np.exp(-t / 0.09)
    sig = np.sin(phase) * env
    sig += _band(_noise(rng, n), 250.0, 1200.0) * env * 0.35
    i0 = int(0.18 * SR)
    tt = np.arange(n - i0) / SR
    phase2 = 2 * np.pi * (120.0 * tt - 90.0 * tt * tt)
    env2 = np.clip(tt / 0.012, 0.0, 1.0) * np.exp(-tt / 0.07)
    sig[i0:] += 0.55 * np.sin(phase2) * env2
    return _normalize(sig)


GENERATORS: Dict[str, Callable[[int], np.ndarray]] = {
    "kiss": make_kiss,
    "breath": make_breath,
    "gasp": make_gasp,
    "suck": make_suck,
    "heartbeat": make_heartbeat,
    "sniff": make_sniff,
    "gulp": make_gulp,
}


def output_path(stem: str, variant: int) -> Path:
    return OUT_DIR / f"{stem}_{variant}.wav"


def make_clip(stem: str, variant: int) -> np.ndarray:
    seed = 17 * variant + sum(ord(c) for c in stem)
    return GENERATORS[stem](seed=seed)


def write_wav(path: Path, mono: np.ndarray, sr: int = SR) -> None:
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def plan(force: bool = False, only: str = "") -> List[Tuple[str, int, Path]]:
    rows: List[Tuple[str, int, Path]] = []
    for stem in GENERATORS:
        if only and stem != only:
            continue
        for variant in range(1, VARIANTS + 1):
            path = output_path(stem, variant)
            if path.exists() and not force:
                print(f"skip (exists): {path.name}")
                continue
            rows.append((stem, variant, path))
    return rows


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing files (including voice clips)")
    ap.add_argument("--only", default="",
                    help="single stem: kiss/breath/gasp/suck/heartbeat/"
                         "sniff/gulp")
    args = ap.parse_args(argv)

    if args.only and args.only not in GENERATORS:
        print(f"Unknown stem {args.only!r}; choose from {sorted(GENERATORS)}")
        return 1

    rows = plan(force=args.force, only=args.only)
    if not rows:
        print("Nothing to do (all foley exists; use --force to regenerate).")
        return 0
    print(f"Plan: {len(rows)} foley clip(s) -> {OUT_DIR}")
    for stem, variant, path in rows:
        print(f"  {path.name}")
    if args.dry_run:
        return 0

    for stem, variant, path in rows:
        sig = make_clip(stem, variant)
        write_wav(path, sig)
        print(f"  ok  {path.name:14s} {sig.size / SR:.2f}s  "
              f"{path.stat().st_size // 1024} KB")
    print("Restart the app once: the SFX folder is scanned at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
