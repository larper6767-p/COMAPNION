#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a private friend-test release of Evelyn into dist/Evelyn/.

Copies ONLY a whitelist of files (never the working copy's private data),
generates clean default state files, runs a secret scan over the result, and
zips it.  Run from the project root:

    .venv\\Scripts\\python tools/build_release.py           # no model (sprite mode)
    .venv\\Scripts\\python tools/build_release.py --with-model

The characters/ tree (Live2D model) is excluded by default because its license
must be checked first.  Without it the app ships with live2d.json set to
"face_renderer": "sprites" and shows a clear "model missing" note.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
STAGE = DIST / "Evelyn"
ZIP_PATH = DIST / "Evelyn.zip"

# ── Whitelist of relpaths to always copy ─────────────────────────────────────
# src/ and config/ are walked with their own rules below; this list covers the
# loose root files.
LOOSE_FILES = [
    "README.md",
    "READMES/README_FIRST.md",
    "READMES/README.md",
    ".env.example",
    "requirements.lock",
    "setup.bat",
    "run.bat",
    "diagnose.bat",
    "tools/diagnose_live2d.py",   # friendly diagnostic (see Stage E of plan)
]

# tools copied from the working tree (exclude probe/scratch output)
TOOLS_KEEP = ["diagnose_live2d.py"]

DIRS_WITH_FILES = ["src", "config", "tools"]

# per-directory exclusion rules (relative names, applied recursively)
EXCLUDE_DIRS = {"__pycache__", "episodes", "archive", "logs", ".pytest_cache", ".git"}
EXCLUDE_FILES = {
    ".env", "memory.json", "relationship.json", "pet_state.json",
    "threads_state.json", "websearch_cache.json", "personality.local.json",
    "debug_out.txt", "diagnose_out.txt", "probe_out.txt",
}

# ── Personal / secret fingerprints (Stage B scan) ───────────────────────────
# Your Fish Audio voice id (must never ship to friends).
VOICE_ID = "83c19893c4974594839bd2d101b1fd66"
# Names / location to scrub (edit if you want to add more).  Case-insensitive
# substring match on the file contents.
# NOTE: "evelyn" is deliberately NOT here — it is the project's own name and
# appears legitimately in README/bat files.  Keep only genuinely personal terms.
PERSONAL_TERMS = ["bori"]  # adjust as needed
# API-key-like patterns.  The last pattern requires a QUOTED literal value after
# the '=', so it does NOT flag `os.environ.get("...API_KEY")` or `api_key=api_key`
# source-code references (those have unquoted variable names as values).
KEY_RE = [
    re.compile(r"\b(?:sk|pk|key|token)-?[A-Za-z0-9_\-]{16,}\b", re.I),
    re.compile(r"(?:api[_-]?key|secret|token|sk|pk)\s*[:=]\s*['\"]([A-Za-z0-9_\-./+]{12,})['\"]", re.I),
]
# NOTE: there is deliberately no bare \b[0-9a-f]{32}\b rule.  The Fish voice id
# is already caught by the explicit VOICE_ID check above, and a bare-32-hex rule
# false-positives on the many UUIDs inside the Live2D model config (vtube.json).

EXT_SCAN = {".py", ".json", ".txt", ".md", ".example", ".bat", ".lock"}


def clean_config(src_dir: Path) -> None:
    """Copy config/ but with clean, friend-safe state files."""
    cfg = STAGE / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.name in EXCLUDE_FILES or item.name in EXCLUDE_DIRS:
            continue
        if item.is_dir():
            shutil.copytree(item, cfg / item.name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, cfg / item.name)

    # Clean defaults (never your private memory/relationship/window state).
    _write_json(cfg / "memory.json", [])
    _write_json(cfg / "relationship.json", {"closeness": 0})
    _write_json(cfg / "pet_state.json", {"idle_enabled": True, "liveliness": 0.7,
                                         "behavior_director": True})


def copy_src(src_dir: Path) -> None:
    """Copy src/ recursively, minus caches."""
    for item in src_dir.rglob("*"):
        if item.name in EXCLUDE_DIRS or any(p.name in EXCLUDE_DIRS
                                            for p in item.relative_to(src_dir).parents):
            continue
        rel = item.relative_to(src_dir)
        dest = STAGE / "src" / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def copy_tools(tools_dir: Path) -> None:
    for name in TOOLS_KEEP:
        p = tools_dir / name
        if p.exists():
            dest = STAGE / "tools" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)


def copy_characters(with_model: bool) -> None:
    chars = STAGE / "characters"
    if not with_model:
        return
    src = ROOT / "characters"
    for item in src.rglob("*"):
        if item.name in EXCLUDE_DIRS or any(p.name in EXCLUDE_DIRS
                                            for p in item.relative_to(src).parents):
            continue
        rel = item.relative_to(src)
        dest = chars / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def scrub_voice_id() -> None:
    """Blank the real Fish voice id in the staged copies so it never ships.

    The value is hardcoded in src/core/config.py (preset_voice_id) AND in
    config/personality.json (voice_id).  We replace it with an empty string in
    the staged tree; the first-run setup will still warn if no voice is set.
    This keeps the build private even before the user finishes scrubbing source.
    """
    targets = [
        STAGE / "src" / "core" / "config.py",
        STAGE / "config" / "personality.json",
    ]
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if VOICE_ID in text:
            path.write_text(text.replace(VOICE_ID, ""), encoding="utf-8")
            print(f"  scrubbed voice id from {path.relative_to(STAGE)}")


def write_env_example() -> None:
    (STAGE / ".env.example").write_text(
        "# Copy this file to .env and fill in your own keys.\n"
        "# Fish Audio: https://fish.audio  (voice synthesis)\n"
        "FISH_API_KEY=your_fish_api_key_here\n"
        "# Cerebras: https://cloud.cerebras.ai  (LLM brain)\n"
        "CEREBRAS_API_KEY=your_cerebras_api_key_here\n",
        encoding="utf-8")


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Secret scan (Stage B) ────────────────────────────────────────────────────
def scan_secrets() -> list:
    findings = []
    for path in STAGE.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXT_SCAN:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        low = text.lower()
        if VOICE_ID in text:
            findings.append(f"{path}: contains Fish voice ID")
        for term in PERSONAL_TERMS:
            # whole-word match, so a name like "bori" doesn't fire on "boring"
            if re.search(rf"\b{re.escape(term.lower())}\b", low):
                findings.append(f"{path}: contains personal term {term!r}")
        for pat in KEY_RE:
            for m in pat.finditer(text):
                # skip the .env.example placeholders
                snippet = m.group(0)
                if "your_" in snippet or "here" in snippet:
                    continue
                findings.append(f"{path}: possible secret {snippet[:24]}…")
    return findings


def build(with_model: bool) -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True, exist_ok=True)

    copy_src(ROOT / "src")
    clean_config(ROOT / "config")
    copy_tools(ROOT / "tools")
    copy_characters(with_model)
    write_env_example()
    scrub_voice_id()

    # loose files
    for name in LOOSE_FILES:
        p = ROOT / name
        if p.exists():
            dest = STAGE / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)

    # If no model, force sprite mode so the app starts cleanly + note file.
    live2d_cfg = STAGE / "config" / "live2d.json"
    if live2d_cfg.exists() and not with_model:
        data = json.loads(live2d_cfg.read_text(encoding="utf-8"))
        data["face_renderer"] = "sprites"
        live2d_cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        (STAGE / "MODEL_NOT_INCLUDED.txt").write_text(
            "This build ships without the Live2D model files (license pending).\n"
            "The app runs in sprite (image) mode.  See READMES/README_FIRST.md.\n",
            encoding="utf-8")


def zip_stage() -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in STAGE.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(STAGE))


def main() -> int:
    # Console on some Windows code pages can't encode the Chinese model filename
    # (简.model3.json) in paths.  Force UTF-8 so reporting never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Build friend-test release")
    ap.add_argument("--with-model", action="store_true",
                    help="include characters/ (Live2D model) — check licence first")
    args = ap.parse_args()

    print(f"Building into {STAGE}  (with_model={args.with_model})")
    build(args.with_model)

    print("Running secret scan over the staged tree…")
    findings = scan_secrets()
    if findings:
        print("\nSECRET SCAN FAILED — the following were found in the build:")
        for f in findings:
            print("  -", f)
        print("\nFix the source, then rebuild. Nothing was zipped.")
        return 1

    zip_stage()
    print(f"\nOK — zipped to {ZIP_PATH}")
    print(f"  {ZIP_PATH.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
