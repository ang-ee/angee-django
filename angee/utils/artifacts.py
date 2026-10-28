"""Guarded reconciliation of generated composition artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from angee.utils.files import write_atomic


@dataclass(slots=True)
class GeneratedTree:
    """Synchronize a rendered map of generated text artifacts with a directory.

    ``owns`` scopes both pruning and cleanup. Only owned files, symlinks and
    directories made empty by those removals are deleted. The caller supplies
    any required marker and root guard.
    """

    root: Path
    artifacts: Mapping[Path, str]
    owns: Callable[[Path], bool]
    sentinel: tuple[Path, str] | None = None
    clean_root: Path | None = None

    def drift(self) -> list[Path]:
        """Return missing, changed, and owned orphan artifact paths."""

        changed, orphans = self._changes()
        return sorted(changed | orphans)

    def reconcile(self, *, prune: bool) -> bool:
        """Repair artifacts, optionally pruning owned orphan files and directories.

        Validate a configured guard before writing, so reconciliation cannot turn
        a foreign directory into generated output by writing its sentinel.
        Consumers without a guard rely on ``owns`` to scope surgical pruning.
        """

        self._validate_root(initializing=True)
        changed, orphans = self._changes()
        removed = False
        if prune:
            for relative_path in sorted(orphans, reverse=True):
                path = self.root / relative_path
                if path.is_symlink() or path.is_file():
                    path.unlink()
                    removed = True
                elif path.is_dir():
                    path.rmdir()
                    removed = True
        for relative_path in sorted(changed):
            write_atomic(self.root / relative_path, self.artifacts[relative_path])
        return bool(changed or removed)

    def clean(self) -> tuple[Path, ...]:
        """Delete owned output and return retained paths relative to the root."""

        self._validate_root()
        paths = sorted(self.root.rglob("*"), reverse=True)
        preserved = any(not self.owns(path.relative_to(self.root)) for path in paths)
        for path in paths:
            relative_path = path.relative_to(self.root)
            if not self.owns(relative_path) or (
                preserved and self.sentinel is not None and relative_path == self.sentinel[0]
            ):
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        return tuple(sorted(path.relative_to(self.root) for path in self.root.rglob("*")))

    @staticmethod
    def validate_artifacts(root: Path, artifacts: Mapping[Path, str]) -> None:
        """Reject invalid paths and links escaping the effective generated root."""

        resolved_root = root.resolve()
        for path, text in artifacts.items():
            if not isinstance(path, Path) or path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"Generated artifact must have a root-relative file path: {path!r}")
            if not isinstance(text, str):
                raise ValueError(f"Generated artifact must contain text: {path}")
            if not (root / path).resolve().is_relative_to(resolved_root):
                raise ValueError(f"Generated artifact escapes the root through a symlink: {path}")

    def _changes(self) -> tuple[set[Path], set[Path]]:
        self.validate_artifacts(self.root, self.artifacts)
        expected = set(self.artifacts)
        expected.update(parent for path in self.artifacts for parent in path.parents)
        changed = {
            relative_path
            for relative_path, text in self.artifacts.items()
            if not (path := self.root / relative_path).is_file() or path.read_text(encoding="utf-8") != text
        }
        orphans: set[Path] = set()
        for path in sorted(self.root.rglob("*"), reverse=True):
            relative_path = path.relative_to(self.root)
            if relative_path in expected or not self.owns(relative_path):
                continue
            if path.is_symlink() or path.is_file() or (
                path.is_dir() and all(child.relative_to(self.root) in orphans for child in path.iterdir())
            ):
                orphans.add(relative_path)
        return changed, orphans

    def _validate_root(self, *, initializing: bool = False) -> None:
        """Verify the configured root and existing marker before any mutation."""

        if self.clean_root is not None and self.root.resolve() != self.clean_root.resolve():
            raise RuntimeError(f"{self.root} is not the configured output directory")
        if not self.root.exists():
            return
        if any(self.root.iterdir()) and self.sentinel is not None:
            sentinel_path, marker = self.sentinel
            sentinel_path = self.root / sentinel_path
            if not sentinel_path.is_file() or marker not in sentinel_path.read_text(encoding="utf-8"):
                if initializing:
                    # A source checkout may contain only caller-owned retained
                    # inputs. Initial output may be added without overwriting
                    # an owned file or deleting an owned orphan. Cleanup never
                    # uses this exception and still requires the marker.
                    _changed, orphans = self._changes()
                    owned_files = any(
                        self.owns(path.relative_to(self.root)) and (path.is_file() or path.is_symlink())
                        for path in self.root.rglob("*")
                    )
                    if not orphans and not owned_files:
                        return
                raise RuntimeError(f"{self.root} lacks the required generated-output marker")
