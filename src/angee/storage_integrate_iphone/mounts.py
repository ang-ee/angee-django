"""iPhone-backup implementation of the external storage Mount contract."""

from __future__ import annotations

import logging
import stat as stat_module
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, cast

from django.core.exceptions import ValidationError

from angee.integrate_iphone.backup import BackupError, IosBackup, logical_key, split_logical_key
from angee.storage.exceptions import UploadError
from angee.storage_integrate.mounts import (
    LocalFolderMountBackend,
    MountBrowseResult,
    MountEntry,
    MountLocation,
    validate_local_folder_root,
)

logger = logging.getLogger(__name__)


class IphoneBackupMountBackend(LocalFolderMountBackend):
    """Expose an iPhone backup as its faithful ``domain/relativePath`` tree."""

    key = "iphone_backup"
    label = "iPhone backup"

    @classmethod
    def browse(
        cls,
        *,
        credential: Any | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """Browse host folders and mark only backup roots as mountable."""

        listing = super().browse(credential=credential, token=token)
        return replace(
            listing,
            location=_iphone_backup_location(listing.location),
            entries=tuple(_iphone_backup_location(entry) for entry in listing.entries),
        )

    def __init__(self, integration: Any) -> None:
        """Bind one Mount and reserve its manifest and entry-path caches."""

        super().__init__(integration)
        self._backup_reader: IosBackup | None = None
        self._blob_by_path: dict[str, Path] = {}

    @property
    def backup(self) -> IosBackup:
        """Return the one manifest reader shared by this backend instance."""

        if self._backup_reader is None:
            self.check_source()
        return cast(IosBackup, self._backup_reader)

    def check_source(self) -> None:
        """Validate and cache one readable, unencrypted iPhone backup."""

        root = validate_local_folder_root(str(self.bridge.config.get("root") or ""))
        if self._backup_reader is not None and self._root == root:
            return
        if self._backup_reader is not None:
            self._backup_reader.close()
            self._backup_reader = None
        try:
            backup = IosBackup(root)
        except BackupError as error:
            raise ValidationError(str(error)) from error
        self._root = root
        self._backup_reader = backup

    def iter_entries(self) -> Iterator[MountEntry]:
        """Yield manifest files and cache each logical path's physical blob."""

        self._blob_by_path.clear()
        for manifest_file in self.backup.iter_files():
            try:
                path = logical_key(manifest_file.domain, manifest_file.relative_path)
            except BackupError as error:
                self.scan_errors += 1
                logger.warning("storage.mount.iphone.path: skipped row: %s", error)
                continue
            self.observed_paths.add(path)
            try:
                stat = manifest_file.blob_path.stat()
            except OSError as error:
                self.scan_errors += 1
                logger.warning(
                    "storage.mount.iphone.stat: skipped %s: %s",
                    manifest_file.blob_path,
                    error,
                )
                continue
            if not stat_module.S_ISREG(stat.st_mode):
                self.observed_paths.discard(path)
                continue
            self._blob_by_path[path] = manifest_file.blob_path
            yield MountEntry(
                path=path,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )

    def iter_directories(self) -> Iterator[str]:
        """Yield every manifest directory plus each domain's top-level folder."""

        seen: set[str] = set()
        for domain, relative_path in self.backup.iter_dirs():
            try:
                # A domain root is a directory, not a file key; the sentinel is
                # used only to ask the shared key owner to validate its segment.
                domain_root, _sentinel = split_logical_key(logical_key(domain, "__root__"))
                directory = (
                    logical_key(domain, relative_path) if relative_path else domain_root
                )
            except BackupError as error:
                logger.warning("storage.mount.iphone.directory: skipped row: %s", error)
                continue
            if domain_root not in seen:
                seen.add(domain_root)
                yield domain_root
            if relative_path and directory not in seen:
                seen.add(directory)
                yield directory

    def open_entry(self, entry: MountEntry) -> BinaryIO:
        """Open an entry through the map populated by :meth:`iter_entries`."""

        try:
            blob = self._blob_by_path[entry.path]
        except KeyError as error:
            raise UploadError(f"iPhone backup blob is missing: {entry.path!r}") from error
        try:
            return blob.open("rb")
        except OSError as error:
            raise UploadError(f"could not open iPhone backup file {entry.path!r}: {error}") from error

    def storage_backend_spec(self) -> tuple[str, dict[str, Any]]:
        """Serve reference bytes through the logical-to-physical backend."""

        return "iphone_backup", {"root": str(self.root)}


def _iphone_backup_location(location: MountLocation) -> MountLocation:
    """Require ``Manifest.db`` in addition to the local browser's safety gates."""

    reason = location.blocked_reason
    if not reason and not (Path(location.token) / "Manifest.db").is_file():
        reason = "Folder does not contain Manifest.db"
    return replace(
        location,
        is_mountable=not reason,
        blocked_reason=reason,
    )
