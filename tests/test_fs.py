"""Tests for the shared atomic-write primitive."""

from __future__ import annotations

from pathlib import Path

from angee.fs import write_atomic


def test_write_atomic_creates_file_and_parents(tmp_path: Path) -> None:
    """A nested target and its parent directories are created and written."""

    target = tmp_path / "nested" / "out.txt"
    write_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_atomic_overwrites_and_leaves_no_temp(tmp_path: Path) -> None:
    """An overwrite renames the temp file into place, leaving no litter."""

    target = tmp_path / "out.txt"
    write_atomic(target, "first")
    write_atomic(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
    assert [path.name for path in tmp_path.iterdir()] == ["out.txt"]


def test_write_atomic_skips_unchanged(tmp_path: Path) -> None:
    """Identical content is not rewritten, so the mtime does not move."""

    target = tmp_path / "out.txt"
    write_atomic(target, "same")
    before = target.stat().st_mtime_ns
    write_atomic(target, "same")
    assert target.stat().st_mtime_ns == before
