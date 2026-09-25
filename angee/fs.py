"""Filesystem primitives shared by Angee's runtime and SDL emitters.

A namespace-root utility (peer to :mod:`angee.paths`): both the build-time
composer (:mod:`angee.compose.runtime`) and the GraphQL SDL owner
(:mod:`angee.graphql.sdl`) write generated files through one primitive, so the
atomic-write behaviour lives once.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)

GENERATED_SENTINEL = "# ANGEE GENERATED RUNTIME - DO NOT EDIT"
"""Marker every Angee-generated file carries; the gate before destructive cleanup.

Lives here, beside :func:`write_atomic`, because it is the sentinel for *all*
generated files — Python runtime modules, the GraphQL SDL, and the composed
``runtime/web`` artifacts — so both the composer and the web projector import it
from one namespace-root owner rather than from each other.
"""


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, skipping an unchanged file.

    A concurrent reader (another Django boot importing a generated module, the
    Vite dev server reading the SDL) sees either the old file or the new one,
    never a half-written one: the bytes go to a temp file in the *same*
    directory and are then ``os.replace``-d into place, an atomic rename on the
    one filesystem. The unchanged-file short-circuit preserves the emitters'
    behaviour of not touching a file whose contents already match, so neither
    the autoreloader nor Vite sees a spurious modification. A failed write removes
    its own temp file so a leftover ``.tmp`` never lingers in ``runtime/`` to trip
    drift checks.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            tmp = Path(handle.name)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@dataclass(slots=True)
class GeneratedTree:
    """Synchronize a rendered map of generated text artifacts with a directory.

    ``owns`` scopes orphan detection. Cleanup can require a sentinel and root;
    every ``migrations`` subtree is protected.
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
        """Repair artifacts, optionally pruning owned orphan files.

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

    def _changes(self) -> tuple[set[Path], set[Path]]:
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
