"""Filesystem primitives shared by Angee's runtime and SDL emitters.

A namespace-root utility (peer to :mod:`angee.paths`): both the build-time
composer (:mod:`angee.compose.runtime`) and the GraphQL SDL owner
(:mod:`angee.graphql.sdl`) write generated files through one primitive, so the
atomic-write behaviour lives once.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

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
    every ``migrations`` subtree is protected unless explicitly opted out.
    """

    root: Path
    artifacts: Mapping[Path, str]
    owns: Callable[[Path], bool]
    sentinel: tuple[Path, str] | None = None
    clean_root: Path | None = None
    preserve_migrations: bool = True

    def drift(self) -> list[Path]:
        """Return missing, changed, and owned orphan artifact paths."""

        changed, orphans = self._changes()
        return sorted(changed | orphans)

    def reconcile(self, *, prune: bool) -> bool:
        """Repair artifacts, optionally pruning owned orphans.

        Surgical pruning trusts ``owns`` alone — the sentinel/``clean_root``
        gates guard only the wholesale ``clean``/``reset`` path, so a
        consumer's ``owns`` predicate is its whole safety boundary here.
        """

        changed, orphans = self._changes()
        if prune:
            for relative_path in sorted(orphans):
                (self.root / relative_path).unlink()
        for relative_path in sorted(changed):
            write_atomic(self.root / relative_path, self.artifacts[relative_path])
        return bool(changed or (prune and orphans))

    def reset(self) -> None:
        """Clean the generated root, then recreate it for emission."""

        self.clean()
        self.root.mkdir(parents=True, exist_ok=True)

    def clean(self) -> None:
        """Delete generated output subject to the configured cleanup policy."""

        if self.clean_root is not None and self.root.resolve() != self.clean_root.resolve():
            raise RuntimeError(f"{self.root} is not the configured runtime dir")
        if not self.root.exists():
            return
        if any(self.root.iterdir()) and self.sentinel is not None:
            sentinel_path, marker = self.sentinel
            sentinel_path = self.root / sentinel_path
            if not sentinel_path.is_file() or marker not in sentinel_path.read_text(encoding="utf-8"):
                raise RuntimeError(f"{self.root} is not an Angee runtime directory")
        keep_sentinel = any(self._is_preserved(path.relative_to(self.root)) for path in self.root.rglob("*"))
        for path in sorted(self.root.rglob("*"), reverse=True):
            relative_path = path.relative_to(self.root)
            if self._is_preserved(relative_path) or (
                keep_sentinel and self.sentinel is not None and relative_path == self.sentinel[0]
            ):
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _changes(self) -> tuple[set[Path], set[Path]]:
        changed = {
            relative_path
            for relative_path, text in self.artifacts.items()
            if not (path := self.root / relative_path).is_file() or path.read_text(encoding="utf-8") != text
        }
        orphans = {
            relative_path
            for path in self.root.rglob("*")
            if path.is_file()
            and self.owns(relative_path := path.relative_to(self.root))
            and relative_path not in self.artifacts
            and not self._is_preserved(relative_path)
        }
        return changed, orphans

    def _is_preserved(self, path: Path) -> bool:
        return self.preserve_migrations and "migrations" in path.parts
