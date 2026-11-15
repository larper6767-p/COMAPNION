"""Back up and restore Evelyn's user-owned data.

The app's long-term memory is split between small JSON files, daily episode
transcripts, and the on-disk vector store.  This tool bundles them into one
portable zip and can restore it later.

Run from the project root:
    .venv\\Scripts\\python.exe tools\\backup_data.py
    .venv\\Scripts\\python.exe tools\\backup_data.py --out D:\\EvelynBackups
    .venv\\Scripts\\python.exe tools\\backup_data.py --restore backups\\evelyn-backup-....zip --force

Restore is deliberately conservative: it refuses to overwrite existing files
unless ``--force`` is passed, and with ``--force`` it first writes a
``pre-restore`` backup.  Close the companion before restoring the vector store.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATA_PATHS: Tuple[str, ...] = (
    "config/memory.json",
    "config/relationship.json",
    "config/pet_state.json",
    "config/behavior_state.json",
    "config/threads_state.json",
    "config/episodes",
    "config/archive",
    "memory_store",
)


def collect_items(root: Path = ROOT,
                  paths: Sequence[str] = DEFAULT_DATA_PATHS) -> List[Path]:
    """Existing files under the configured data paths, sorted and unique."""
    root = Path(root).resolve()
    found: List[Path] = []
    seen = set()
    for rel in paths:
        base = (root / rel).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue                    # never follow a path outside the root
        candidates: Iterable[Path]
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = (p for p in base.rglob("*") if p.is_file())
        else:
            continue
        for path in candidates:
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(resolved)
    return sorted(found, key=lambda p: p.as_posix())


def _next_archive(out_dir: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = out_dir / f"{prefix}-{stamp}.zip"
    n = 1
    while candidate.exists():
        candidate = out_dir / f"{prefix}-{stamp}-{n}.zip"
        n += 1
    return candidate


def create_backup(root: Path = ROOT, out_dir: Path | None = None,
                  paths: Sequence[str] = DEFAULT_DATA_PATHS,
                  prefix: str = "evelyn-backup") -> Path:
    """Write a zip of the data files and return its path (may be empty)."""
    root = Path(root).resolve()
    out_dir = Path(out_dir) if out_dir is not None else root / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = collect_items(root, paths)
    archive = _next_archive(out_dir, prefix)
    tmp = archive.with_name(archive.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in items:
                zf.write(path, path.relative_to(root).as_posix())
        os.replace(tmp, archive)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return archive


def _safe_members(root: Path,
                  zf: zipfile.ZipFile) -> List[Tuple[zipfile.ZipInfo, Path]]:
    """Validate every archive member and return (info, target) pairs."""
    root = Path(root).resolve()
    out: List[Tuple[zipfile.ZipInfo, Path]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        raw = info.filename.replace("\\", "/")
        parts = PurePosixPath(raw).parts
        if (not parts or raw.startswith("/") or any(p in ("", ".", "..") for p in parts)
                or (len(parts[0]) == 2 and parts[0][1] == ":")):
            raise ValueError(f"unsafe archive member: {info.filename!r}")
        # Reject Unix symlinks; a backup should contain regular files only.
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"symlink in archive: {info.filename!r}")
        target = (root / Path(*parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"unsafe archive member: {info.filename!r}") from exc
        out.append((info, target))
    return out


def restore_backup(root: Path = ROOT, archive: Path | str = "",
                   force: bool = False) -> List[Path]:
    """Restore a backup into ``root``; return the files written."""
    root = Path(root).resolve()
    archive = Path(archive)
    with zipfile.ZipFile(archive, "r") as zf:
        members = _safe_members(root, zf)
        existing = [target for _info, target in members if target.exists()]
        if existing and not force:
            raise FileExistsError(
                f"{len(existing)} restored file(s) already exist; pass --force "
                "to overwrite (a pre-restore backup will be created)")
        written: List[Path] = []
        for info, target in members:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(target)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT),
                    help="project root (default: the repository root)")
    ap.add_argument("--out", default="",
                    help="backup directory (default: <root>/backups)")
    ap.add_argument("--restore", default="",
                    help="restore this zip instead of creating a backup")
    ap.add_argument("--force", action="store_true",
                    help="allow restore to overwrite existing files")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve() if args.out else root / "backups"

    if args.restore:
        archive = Path(args.restore)
        if not archive.is_absolute():
            archive = root / archive
        if not archive.is_file():
            print(f"backup not found: {archive}")
            return 1
        if args.force:
            try:
                pre = create_backup(root, out_dir, prefix="pre-restore")
                print(f"Pre-restore backup: {pre}")
            except Exception as e:
                print(f"Could not create pre-restore backup: {e}")
                return 1
        try:
            written = restore_backup(root, archive, force=args.force)
        except FileExistsError as e:
            print(str(e))
            return 1
        except Exception as e:
            print(f"Restore failed: {e}")
            return 1
        print(f"Restored {len(written)} file(s) from {archive}")
        for path in written[:20]:
            print(f"  {path.relative_to(root).as_posix()}")
        if len(written) > 20:
            print(f"  ... and {len(written) - 20} more")
        return 0

    archive = create_backup(root, out_dir)
    count = len(collect_items(root))
    if count == 0:
        print(f"No data files found under {root}; wrote an empty {archive}")
        return 1
    print(f"Backed up {count} file(s) to {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
