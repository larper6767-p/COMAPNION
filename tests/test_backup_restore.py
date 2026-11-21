"""Backup/restore tool tests (tools/backup_data.py)."""
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from backup_data import (                                  # noqa: E402
    collect_items, create_backup, restore_backup,
)


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "state" / "episodes").mkdir(parents=True)
    (root / "memory_store").mkdir()
    (root / "state" / "memory.json").write_text(
        '["The user likes tea."]', encoding="utf-8")
    (root / "state" / "episodes" / "2026-01-01.json").write_text(
        '[{"role": "user", "text": "hello"}]', encoding="utf-8")
    (root / "memory_store" / "chroma.sqlite3").write_bytes(b"fake-store")
    return root


def test_backup_contains_user_data(tmp_path):
    root = _make_project(tmp_path)
    archive = create_backup(root, out_dir=tmp_path / "backups")
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "state/memory.json" in names
        assert "state/episodes/2026-01-01.json" in names
        assert "memory_store/chroma.sqlite3" in names
        assert "logs/evelyn.log" not in names
        assert zf.read("state/memory.json").decode() == '["The user likes tea."]'


def test_restore_refuses_to_overwrite_without_force(tmp_path):
    root = _make_project(tmp_path)
    archive = create_backup(root, out_dir=tmp_path / "backups")
    (root / "state" / "memory.json").write_text("changed", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_backup(root, archive)
    assert (root / "state" / "memory.json").read_text() == "changed"


def test_restore_round_trip_with_force(tmp_path):
    root = _make_project(tmp_path)
    archive = create_backup(root, out_dir=tmp_path / "backups")
    (root / "state" / "memory.json").write_text("changed", encoding="utf-8")
    (root / "state" / "episodes" / "2026-01-01.json").unlink()
    written = restore_backup(root, archive, force=True)
    assert len(written) >= 3
    assert (root / "state" / "memory.json").read_text() == '["The user likes tea."]'
    assert (root / "state" / "episodes" / "2026-01-01.json").exists()
    assert (root / "memory_store" / "chroma.sqlite3").read_bytes() == b"fake-store"


def test_restore_rejects_path_traversal(tmp_path):
    root = _make_project(tmp_path)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
    with pytest.raises(ValueError, match="unsafe archive member"):
        restore_backup(root, bad, force=True)
    assert not (tmp_path / "escaped.txt").exists()


def test_collect_items_skips_missing_and_outside_paths(tmp_path):
    root = _make_project(tmp_path)
    items = collect_items(root, ("state/memory.json", "does-not-exist",
                                 "../outside.json"))
    names = [p.relative_to(root).as_posix() for p in items]
    assert names == ["state/memory.json"]


def test_backup_creates_unique_names(tmp_path):
    root = _make_project(tmp_path)
    out = tmp_path / "backups"
    a = create_backup(root, out_dir=out)
    b = create_backup(root, out_dir=out)
    assert a != b
    assert a.exists() and b.exists()
