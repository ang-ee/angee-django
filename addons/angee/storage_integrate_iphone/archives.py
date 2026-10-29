"""Recognize storage-backed iPhone archives through the safe archive and manifest owners."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from angee.integrate_iphone.backup import SQLITE_HEADER_LENGTH, BackupError, IosBackup, is_sqlite_header
from angee.storage.archives import ArchiveError, BoundedReader, archive_entries

_RECOGNITION_READ_LIMIT = 128 * 1024 * 1024
_MANIFEST_READ_LIMIT = 64 * 1024 * 1024


def recognizes_backup(file: Any, *, domain: str, relative_path: str) -> bool:
    """Probe a bounded ZIP for a manifest-resolved SQLite store without reading its body."""

    try:
        with file.open_stream() as stream:
            bounded = BoundedReader(stream, limit=_RECOGNITION_READ_LIMIT)
            with zipfile.ZipFile(cast(BinaryIO, bounded)) as archive:
                return backup_manifest(archive, domain=domain, relative_path=relative_path, budget=bounded) is not None
    except (ArchiveError, BackupError, NotImplementedError, OSError, sqlite3.DatabaseError,
            zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def backup_manifest(
    archive: zipfile.ZipFile, *, domain: str, relative_path: str, budget: BoundedReader | None = None,
) -> str | None:
    """Return the first safe manifest containing the requested store, in sorted order.

    Each candidate gets the same byte budget; recognition materializes at most a
    64 MiB manifest and reads only a SQLite header from its resolved store.
    """

    entries = archive_entries(archive)
    for name in sorted(name for name in entries if PurePosixPath(name).name == "Manifest.db"):
        if budget is not None:
            budget.remaining = _RECOGNITION_READ_LIMIT
        manifest = entries[name]
        if manifest.file_size > _MANIFEST_READ_LIMIT:
            continue
        with tempfile.TemporaryDirectory(prefix="angee-iphone-probe-") as temporary:
            root = Path(temporary)
            (root / "Manifest.db").write_bytes(archive.read(manifest))
            backup = IosBackup(root)
            try:
                blob = backup.blob_relative_path(domain, relative_path)
            finally:
                backup.close()
        store = entries.get((PurePosixPath(name).parent / blob).as_posix())
        if store is not None:
            with archive.open(store) as stream:
                if is_sqlite_header(stream.read(SQLITE_HEADER_LENGTH)):
                    return name
    return None
