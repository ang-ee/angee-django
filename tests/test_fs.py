"""Tests for the shared atomic-write primitive."""

from __future__ import annotations

from pathlib import Path

import pytest

from angee.utils.artifacts import GeneratedTree
from angee.utils.files import write_atomic


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


def test_generated_tree_reconcile_respects_ownership_and_prune_policy(tmp_path: Path) -> None:
    """Reconciliation repairs artifacts and prunes only opted-in orphan paths."""

    root = tmp_path / "generated"
    root.mkdir()
    for name in ("expected.txt", "orphan.txt", "notes.md"):
        (root / name).write_text("stale", encoding="utf-8")
    tree = GeneratedTree(root, {Path("expected.txt"): "current"}, owns=lambda path: path.suffix == ".txt")

    assert tree.reconcile(prune=False) is True
    assert (root / "expected.txt").read_text(encoding="utf-8") == "current"
    assert (root / "orphan.txt").exists() and (root / "notes.md").exists()
    assert tree.drift() == [Path("orphan.txt")]
    assert tree.reconcile(prune=False) is False
    assert tree.reconcile(prune=True) is True
    assert [(root / name).exists() for name in ("expected.txt", "orphan.txt", "notes.md")] == [True, False, True]


@pytest.mark.parametrize("has_file", [False, True])
def test_generated_tree_reconcile_converges_with_orphan_directories(tmp_path: Path, has_file: bool) -> None:
    """Reconciliation removes reported directories after their owned children."""

    nested = Path("retired") / "nested"
    (tmp_path / nested).mkdir(parents=True)
    orphan = nested / "stale.txt"
    if has_file:
        (tmp_path / orphan).write_text("stale", encoding="utf-8")
    tree = GeneratedTree(tmp_path, {}, owns=lambda path: True)

    assert tree.drift() == [Path("retired"), nested] + ([orphan] if has_file else [])
    assert tree.reconcile(prune=True) is True
    assert tree.drift() == []
    assert tree.reconcile(prune=True) is False
    assert not (tmp_path / "retired").exists()


@pytest.mark.parametrize("preserved", [Path("kept/notes.md"), Path("kept/migrations/0001.py")])
def test_generated_tree_reconcile_preserves_unowned_and_migration_directories(
    tmp_path: Path, preserved: Path,
) -> None:
    """A preserved descendant protects its parents during orphan pruning."""

    retained = tmp_path / preserved
    retained.parent.mkdir(parents=True)
    retained.write_text("keep", encoding="utf-8")
    orphan = Path("kept/orphan")
    (tmp_path / orphan).mkdir()
    tree = GeneratedTree(tmp_path, {}, owns=lambda path: path.suffix != ".md")

    assert tree.drift() == [orphan]
    assert tree.reconcile(prune=True) is True
    assert retained.read_text(encoding="utf-8") == "keep"
    assert tree.drift() == []


def test_generated_tree_cleanup_guards_and_migration_preservation(tmp_path: Path) -> None:
    """Cleanup requires the configured generated root and preserves all migrations."""

    root = tmp_path / "generated"
    root.mkdir()
    (root / "__init__.py").write_text("# wrong\n", encoding="utf-8")
    sentinel = (Path("__init__.py"), "# generated")

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        GeneratedTree(root, {}, owns=lambda path: True, sentinel=sentinel).clean()

    (root / "__init__.py").write_text("# generated\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not the configured runtime dir"):
        GeneratedTree(root, {}, owns=lambda path: True, sentinel=sentinel, clean_root=tmp_path / "other").clean()

    migration = root / "app" / "migrations" / "archive" / "snapshot.txt"
    migration.parent.mkdir(parents=True)
    migration.write_text("keep", encoding="utf-8")
    (root / "stale.txt").write_text("delete", encoding="utf-8")

    GeneratedTree(root, {}, owns=lambda path: True, sentinel=sentinel).clean()
    assert [migration.exists(), (root / "__init__.py").exists(), (root / "stale.txt").exists()] == [True, True, False]


def test_generated_tree_drift_never_reports_preserved_migration_files(tmp_path: Path) -> None:
    """A stray non-.py file under a live migrations/ dir is preserved, not drift.

    Pins the post-GeneratedTree semantics: everything with ``migrations`` in its
    parts is outside the drift set (the old Runtime reported such files stale,
    breaking ``--check`` for e.g. django-linear-migrations' max_migration.txt).
    """

    root = tmp_path / "generated"
    (root / "app" / "migrations").mkdir(parents=True)
    (root / "app" / "migrations" / "max_migration.txt").write_text("0002_x", encoding="utf-8")
    tree = GeneratedTree(root, {Path("app") / "models.py": "code"}, owns=lambda path: True)
    assert tree.drift() == [Path("app") / "models.py"]


@pytest.mark.parametrize("operation", ["drift", "reconcile"])
def test_generated_tree_rejects_artifacts_through_escaping_directory_symlinks(
    tmp_path: Path, operation: str,
) -> None:
    """Every path is checked before boot repair can write through a directory link."""

    root = tmp_path / "generated"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    foreign = outside / "models.py"
    foreign.write_text("foreign", encoding="utf-8")
    (root / "app").symlink_to(outside, target_is_directory=True)
    tree = GeneratedTree(root, {
        Path("first.txt"): "new",
        Path("app/models.py"): "replacement",
    }, owns=lambda path: True)

    with pytest.raises(ValueError, match="escapes the root through a symlink"):
        if operation == "drift":
            tree.drift()
        else:
            tree.reconcile(prune=False)
    assert foreign.read_text(encoding="utf-8") == "foreign"
    assert not (root / "first.txt").exists()


def test_generated_tree_accepts_a_configured_symlink_root(tmp_path: Path) -> None:
    """Containment follows the configured root itself to its intended destination."""

    destination = tmp_path / "destination"
    destination.mkdir()
    root = tmp_path / "generated"
    root.symlink_to(destination, target_is_directory=True)
    tree = GeneratedTree(root, {Path("app/models.py"): "generated"}, owns=lambda path: True, clean_root=root)

    assert tree.reconcile(prune=False) is True
    assert (destination / "app/models.py").read_text(encoding="utf-8") == "generated"
    assert tree.drift() == []
