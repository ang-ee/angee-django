"""Read-only storage backend for logical paths inside an iPhone backup."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation

from angee.integrate_iphone.backup import BackupError, IosBackup, split_logical_key
from angee.storage.backends import StorageBackend

logger = logging.getLogger(__name__)


class IphoneBackupStorageBackend(StorageBackend):
    """Map logical ``domain/relativePath`` keys to content-addressed blobs."""

    writable: ClassVar[bool] = False

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the backup root without retaining a thread-affine connection."""

        super().__init__(backend_config=backend_config)
        root = str(self.backend_config.get("root") or "").strip()
        if not root:
            raise ImproperlyConfigured(
                "IphoneBackupStorageBackend requires backend_config['root']."
            )
        self.root = Path(root)

    def _open(self, name: str, mode: str = "rb") -> BinaryIO:
        """Open one logical key as a binary physical blob stream."""

        if mode not in {"r", "rb"}:
            raise OSError("iPhone backup storage is read-only")
        return self._blob_path(name).open("rb")

    def _save(self, name: str, content: Any) -> str:
        """Refuse writes to externally-owned backup bytes."""

        del name, content
        raise OSError("iPhone backup storage is read-only")

    def delete(self, name: str) -> None:
        """Refuse direct deletion of externally-owned backup bytes."""

        del name
        raise OSError("iPhone backup storage is read-only")

    def exists(self, name: str) -> bool:
        """Return whether a logical key resolves to an existing backup blob."""

        return self._resolve_blob(name) is not None

    def size(self, name: str) -> int:
        """Return the physical blob size for one logical key."""

        return self._blob_path(name).stat().st_size

    def url(self, name: str) -> str:
        """Refuse public URLs; bytes are served through Angee's proxy."""

        del name
        raise ValueError("iPhone backup storage is not URL-accessible")

    def presigned_get(self, key: str, *, expires_in: int) -> str | None:
        """Use the authenticated Angee proxy for every download."""

        del key, expires_in
        return None

    def discard(self, key: str, *, context: str) -> None:
        """Leave externally-owned backup bytes untouched when rows are purged."""

        logger.info("storage.%s: retained iPhone backup object %s", context, key)

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
