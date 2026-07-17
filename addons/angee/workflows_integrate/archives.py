"""Bounded ZIP inspection and safe staging for archive extractor clients.

The workflows-integrate bridge owns archive mechanics shared by vendor
extractors: one aggregate byte budget during recognition, normalized unique
member names, subtree selection, and deterministic extraction that rejects
traversal and symbolic links. Vendor addons remain responsible for recognizing
their own archive vocabulary and translating staged data into domain ingest.
"""

from __future__ import annotations

import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO

__all__ = (
    "EXTRACT_DECLARED_LIMIT",
    "ArchiveError",
    "BoundedReader",
    "archive_entries",
    "extract_archive",
    "safe_member_name",
    "stage_subtree",
    "subtree_entries",
)

EXTRACT_DECLARED_LIMIT = 128 * 1024 * 1024 * 1024
"""Aggregate declared uncompressed bytes accepted for one staged subtree."""


class ArchiveError(Exception):
    """An archive cannot be inspected or staged within the bridge contract."""


class BoundedReader:
    """Seekable binary-stream proxy enforcing one aggregate read budget.

    ZIP recognition runs once per registered extractor, so every probe must use
    an explicit byte budget and must never issue an unbounded read. Callers may
    reset :attr:`remaining` between independent candidates inside one archive.
    """

    def __init__(self, stream: BinaryIO, *, limit: int) -> None:
        self.stream = stream
        self.remaining = limit

    def read(self, size: int = -1) -> bytes:
        """Read at most the remaining budget, rejecting unbounded requests."""

        if size < 0 or size > self.remaining:
            raise ArchiveError("Archive recognition exceeded its bounded read budget.")
        value = self.stream.read(size)
        self.remaining -= len(value)
        return value

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek without spending the byte-read budget."""

        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        """Return the wrapped stream position."""

        return self.stream.tell()

    def close(self) -> None:
        """Close the wrapped stream."""

        self.stream.close()

    def readable(self) -> bool:
        """Return whether the wrapped stream can be read."""

        return self.stream.readable()

    def seekable(self) -> bool:
        """Return whether the wrapped stream supports ZIP random access."""

        return self.stream.seekable()


def archive_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Return safe, unique archive members keyed by normalized POSIX path."""

    entries: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        name = safe_member_name(info.filename)
        if name in entries:
            raise ArchiveError(f"Archive repeats member {name!r}.")
        entries[name] = info
    return entries


def safe_member_name(value: str) -> str:
    """Return a normalized relative member path or reject root traversal."""

    if "\\" in value:
        raise ArchiveError(f"Archive member {value!r} is not a POSIX path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ArchiveError(f"Archive member {value!r} escapes its archive root.")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        raise ArchiveError("Archive contains an empty member path.")
    return PurePosixPath(*parts).as_posix()


def subtree_entries(
    entries: dict[str, zipfile.ZipInfo],
    parent: PurePosixPath,
) -> dict[str, zipfile.ZipInfo]:
    """Return only archive members inside ``parent``."""

    if str(parent) == ".":
        return entries
    prefix = parent.as_posix() + "/"
    return {name: info for name, info in entries.items() if name.startswith(prefix)}


def extract_archive(
    archive: zipfile.ZipFile,
    root: Path,
    *,
    entries: dict[str, zipfile.ZipInfo],
) -> None:
    """Extract normalized files deterministically without following links."""

    for name, info in sorted(entries.items()):
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ArchiveError(f"Archive member {name!r} is a symbolic link.")
        target = root.joinpath(*PurePosixPath(name).parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


@contextmanager
def stage_subtree(
    archive: zipfile.ZipFile,
    parent: PurePosixPath,
) -> Iterator[Path]:
    """Safely stage one archive subtree under the bridge's declared-size cap.

    The bridge owns the complete generic staging lifecycle: normalized member
    inventory, subtree selection, aggregate declared-size enforcement,
    deterministic extraction, and temporary-directory cleanup. The yielded
    path is the selected subtree root, ready for a vendor-specific delegate.
    """

    entries = subtree_entries(archive_entries(archive), parent)
    declared = sum(info.file_size for info in entries.values())
    if declared > EXTRACT_DECLARED_LIMIT:
        raise ArchiveError("Archive subtree exceeds the supported extraction size.")
    with tempfile.TemporaryDirectory(prefix="angee-archive-stage-") as temporary:
        root = Path(temporary)
        extract_archive(archive, root, entries=entries)
        yield root if str(parent) == "." else root.joinpath(*parent.parts)
