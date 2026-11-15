"""Feature 12b: generate her SFX clips with HER OWN TTS voice.

The problem this solves: assets/sfx ships empty, and no stock sound pack
matches her Fish Audio voice.  This tool synthesises the short non-verbal
cues with the SAME client/voice/model the app speaks with, so the clips
sound like Eve and blend under her speech.

VOCALISATIONS ONLY.  Kisses/breaths/gasps/sucks/heartbeats/sniffs/gulps are
non-vocal foley and live in tools/gen_sfx_foley.py — TTS can only
articulate words, so asking Fish for a lip-smack just produces speech.
(Those foley stems are deliberately absent here so --force can never
overwrite them with spoken words.)

    .venv\\Scripts\\python.exe tools\\gen_sfx.py            # generate all
    .venv\\Scripts\\python.exe tools\\gen_sfx.py --dry-run   # show plan
    .venv\\Scripts\\python.exe tools\\gen_sfx.py --only giggle --force

Each prompt is synthesised to a temp mp3 (Fish API), decoded, trimmed of
leading/trailing silence, peak-normalised, and written as
    assets/sfx/<stem>_<n>.wav
Existing files are skipped unless --force.  The runtime SfxPlayer picks
the stem automatically (config/sfx.yaml); restart the app once after
generating.

Note: TTS voices are trained on speech — pure noises (breaths, giggles)
are best-effort.  Listen once, delete the weak ones, regenerate with
--force/--only as needed.  The player skips any stem that has no file.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT_DIR = ROOT / "assets" / "sfx"

# Each stem maps to candidate spoken prompts; several files per stem make
# the runtime's random pick feel alive.  Stems must match config/sfx.yaml.
# Feature 12d: only VOCAL cues here (foley lives in gen_sfx_foley.py).
PROMPTS: Dict[str, List[str]] = {
    "heart":  ["Mmm~", "Mmh~", "Mmmm~"],
    "tender": ["Mmm...", "Mmh—", "Mmh, mmm~"],
    "giggle": ["Hehe~", "Hehehehe~", "Hehehe~ hehe~"],
    "moan":   ["Mmmh~", "Ahhn~", "Mmm... aah~"],
    "whimper": ["Hnnng~", "Nnng~"],
    "sigh":   ["Haaah...", "Aahh..."],
    "laugh":  ["Hehehe~", "Ahaha~"],
    "purr":   ["Prrrr~", "Mmmrrr~"],
}

# Fish sometimes hallucinates minute-long audio for non-verbal prompts;
# a sound cue longer than this is junk and gets discarded, not shipped.
MAX_CLIP_S = 4.0

# One synth per prompt; per-stem speed nudges pitch/pace slightly.
SPEED: Dict[str, float] = {"sigh": 0.95, "purr": 0.95}

CLIP_SR = 44100
CLIP_CHANNELS = 2          # decode format must match the mixer init below
TRIM_THRESHOLD = 0.01      # amplitude below this = silence
TRIM_PAD_S = 0.03          # keep a little air around the cue
TARGET_PEAK = 0.8          # normalise so all cues sit at a similar level


def trim_bounds(mono: np.ndarray, threshold: float = TRIM_THRESHOLD,
                pad: int = 0) -> Tuple[int, int]:
    """Pure helper: first/last sample index above ``threshold`` (padded).

    Returns (start, end) with ``end`` exclusive.  Falls back to the full
    range when everything is below threshold (never returns an empty pick).
    """
    if mono.size == 0:
        return 0, 0
    loud = np.flatnonzero(np.abs(mono) >= threshold)
    if loud.size == 0:
        return 0, int(mono.size)
    start = max(0, int(loud[0]) - pad)
    end = min(int(mono.size), int(loud[-1]) + 1 + pad)
    return start, end


def output_path(stem: str, index: int) -> Path:
    return OUT_DIR / f"{stem}_{index}.wav"


def plan(force: bool = False, only: str = "") -> List[Tuple[str, int, str, Path]]:
    """(stem, index, prompt, path) rows, respecting skip-existing."""
    rows: List[Tuple[str, int, str, Path]] = []
    for stem, prompts in PROMPTS.items():
        if only and stem != only:
            continue
        for i, prompt in enumerate(prompts, start=1):
            path = output_path(stem, i)
            if path.exists() and not force:
                print(f"skip (exists): {path.name}")
                continue
            rows.append((stem, i, prompt, path))
    return rows


def _decode_mp3_to_mono(path: Path) -> Tuple[np.ndarray, int]:
    """Decode an mp3 via pygame's mixer (dummy driver, no playback)."""
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame                     # lazy: only the generator needs it
    if not pygame.mixer.get_init():
        pygame.mixer.init(frequency=CLIP_SR, size=-16, channels=CLIP_CHANNELS)
    sound = pygame.mixer.Sound(str(path))
    raw = sound.get_raw()
    arr = np.frombuffer(raw, dtype=np.int16)
    if CLIP_CHANNELS > 1:
        arr = arr.reshape(-1, CLIP_CHANNELS).mean(axis=1)
    return arr.astype(np.float32) / 32768.0, CLIP_SR


def _write_wav(path: Path, mono: np.ndarray, sr: int) -> None:
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def synthesise_clip(client, voice_id: str, model: str, prompt: str,
                    speed: float, out: Path) -> float:
    """One Fish call -> trimmed, normalised wav.  Returns duration seconds."""
    from fishaudio.utils import save
    tmp = out.with_suffix(".tmp.mp3")
    try:
        gen = client.tts.convert(text=prompt, reference_id=voice_id,
                                 model=model, speed=speed)
        save(gen, str(tmp))
        mono, sr = _decode_mp3_to_mono(tmp)
        start, end = trim_bounds(mono, TRIM_THRESHOLD,
                                 int(TRIM_PAD_S * sr))
        seg = mono[start:end].copy()
        if seg.size == 0:
            raise ValueError("empty after trim")
        if seg.size / sr > MAX_CLIP_S:
            raise ValueError(
                f"clip too long ({seg.size / sr:.1f}s > {MAX_CLIP_S}s) — "
                "discarded as a hallucination"
            )
        peak = float(np.abs(seg).max()) or 1.0
        seg *= (TARGET_PEAK / peak)
        _write_wav(out, seg, sr)
        return seg.size / sr
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="",
                    help="single vocal stem: heart/tender/giggle/breath/"
                         "moan/whimper/sigh/laugh/purr")
    args = ap.parse_args(argv)

    rows = plan(force=args.force, only=args.only)
    if not rows:
        print("Nothing to do (all clips exist; use --force to regenerate).")
        return 0
    print(f"Plan: {len(rows)} clip(s) -> {OUT_DIR}")
    for stem, i, prompt, path in rows:
        print(f"  {stem}_{i}: {prompt!r}")
    if args.dry_run:
        return 0

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        load_dotenv()
    except ImportError:
        pass
    if not os.environ.get("FISH_API_KEY"):
        print("ERROR: FISH_API_KEY missing (add it to .env).")
        return 1
    import json
    persona = json.loads((ROOT / "config" / "personality.json")
                         .read_text(encoding="utf-8"))
    voice_id = persona.get("voice_id", "")
    model = persona.get("tts_model", "s2.1-pro-free")

    from fishaudio import FishAudio
    client = FishAudio(timeout=90)

    failures = 0
    for stem, i, prompt, path in rows:
        try:
            dur = synthesise_clip(client, voice_id, model, prompt,
                                  SPEED.get(stem, 1.0), path)
            print(f"  ok  {path.name:14s} {dur:.2f}s  {path.stat().st_size//1024} KB")
        except Exception as e:
            failures += 1
            print(f"  FAIL {stem}_{i}: {type(e).__name__}: {e}")
        time.sleep(0.4)               # be kind to the free tier
    print(f"Done: {len(rows) - failures} ok, {failures} failed.")
    print("Restart the app once: the SFX folder is scanned at startup.")
    return 1 if failures and failures == len(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
