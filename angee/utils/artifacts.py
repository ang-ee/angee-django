"""Guarded reconciliation of generated composition artifacts."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from angee.utils.files import write_atomic

logger = logging.getLogger(__name__)

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Stable marker checked before generated runtime cleanup."""


@dataclass(slots=True)
class GeneratedTree:
    """Synchronize a rendered map of generated text artifacts with a directory.

    ``owns`` scopes orphan detection. Pruning removes owned files, symlinks,
    and directories made empty by those removals. Cleanup can require a sentinel
    and root; every ``migrations`` subtree is protected.
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

        Validate a configured guard before writing, so boot repair cannot turn
        a foreign directory into generated output by writing its sentinel.
        Consumers without a guard rely on ``owns`` to scope surgical pruning.
        """

        self._validate_root()
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

    def reset(self) -> None:
        """Clean the generated root, then recreate it for emission."""

        self.clean()
        self.root.mkdir(parents=True, exist_ok=True)

    def clean(self, *, current_roots: Iterable[str] = ()) -> None:
        """Clean the guarded root; report history outside current artifact roots.

        ``current_roots`` supplies known roots when cleanup has no source map.
        """

        self._validate_root()
        paths = sorted(self.root.rglob("*"), reverse=True)
        preserved = sorted({
            Path(*relative_path.parts[:relative_path.parts.index("migrations") + 1])
            for path in paths
            if self._is_preserved(relative_path := path.relative_to(self.root))
        })
        for path in paths:
            relative_path = path.relative_to(self.root)
            if self._is_preserved(relative_path) or (
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
        roots = {path.parts[0] for path in self.artifacts} | set(current_roots)
        retired = [path for path in preserved if path.parts[0] not in roots]
        if retired:
            logger.warning(
                "Preserved migration directories during cleanup: %s. "
                "Review their database history before an explicitly authorized removal.",
                ", ".join(str(self.root / path) for path in retired),
            )

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
            if relative_path in expected or not self.owns(relative_path) or self._is_preserved(relative_path):
                continue
            if path.is_symlink() or path.is_file() or (
                path.is_dir() and all(child.relative_to(self.root) in orphans for child in path.iterdir())
            ):
                orphans.add(relative_path)
        return changed, orphans

    def _is_preserved(self, path: Path) -> bool:
        return "migrations" in path.parts

    def _validate_root(self) -> None:
        """Verify the configured root and existing marker before any mutation."""

        if self.clean_root is not None and self.root.resolve() != self.clean_root.resolve():
            raise RuntimeError(f"{self.root} is not the configured runtime dir")
        if not self.root.exists():
            return
        if any(self.root.iterdir()) and self.sentinel is not None:
            sentinel_path, marker = self.sentinel
            sentinel_path = self.root / sentinel_path
            if not sentinel_path.is_file() or marker not in sentinel_path.read_text(encoding="utf-8"):
                raise RuntimeError(f"{self.root} is not an Angee runtime directory")
