"""Read-only storage backend for logical paths inside an iPhone backup."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation

from angee.integrate_iphone.backup import BackupError, IosBackup, split_logical_key
from angee.storage_integrate.backends import ExternalStorageBackend


class IphoneBackupStorageBackend(ExternalStorageBackend):
    """Map logical ``domain/relativePath`` keys to content-addressed blobs."""

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the backup root without retaining a thread-affine connection."""

        super().__init__(backend_config=backend_config)
        root = str(self.backend_config.get("root") or "").strip()
        if not root:
            raise ImproperlyConfigured(
                "IphoneBackupStorageBackend requires backend_config['root']."
            )
        self.root = Path(root)

    def open_external(self, name: str) -> BinaryIO:
        """Open one logical key as a binary physical blob stream."""

        return self._blob_path(name).open("rb")

    def exists(self, name: str) -> bool:
        """Return whether a logical key resolves to an existing backup blob."""

        return self._resolve_blob(name) is not None

    def size(self, name: str) -> int:
        """Return the physical blob size for one logical key."""

        return self._blob_path(name).stat().st_size

    def _blob_path(self, name: str) -> Path:
        """Resolve one safe logical storage key to its physical blob."""

        blob = self._resolve_blob(name)
        if blob is None:
            raise FileNotFoundError(name)
        return blob

    def _resolve_blob(self, name: str) -> Path | None:
        """Resolve through a short-lived manifest reader safe for this thread."""

        try:
            domain, relative_path = split_logical_key(name)
        except BackupError as error:
            raise SuspiciousFileOperation(
                f"Invalid iPhone backup storage key: {name!r}"
            ) from error
        backup = IosBackup(self.root)
        try:
            return backup.blob_path(domain, relative_path)
        finally:
            backup.close()
