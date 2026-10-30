"""Interpret validated ZIP members as unencrypted iPhone backup manifests."""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from .backup import SQLITE_HEADER_LENGTH, IosBackup, is_sqlite_header

_MANIFEST_READ_LIMIT = 64 * 1024 * 1024


def backup_manifest(
    archive: zipfile.ZipFile, entries: Mapping[str, zipfile.ZipInfo], *, domain: str, relative_path: str,
    before_candidate: Callable[[], None] | None = None,
) -> str | None:
    """Return the first safe manifest containing the requested store, in sorted order.

    The caller validates archive members and owns byte budgets; before_candidate
    lets it reset a bounded stream before each independent manifest. This reader
    materializes at most a 64 MiB manifest and reads only the resolved store header.
    """

    for name in sorted(name for name in entries if PurePosixPath(name).name == "Manifest.db"):
        if before_candidate is not None:
            before_candidate()
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
