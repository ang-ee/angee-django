"""Read-only storage backend serving a reference Google Drive mount's bytes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, BinaryIO, ClassVar

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from angee.storage.backends import StorageBackend
from angee.storage_integrate_gdrive.client import GoogleDriveClient
from angee.storage_integrate_gdrive.mounts import GoogleDriveMountBackend

logger = logging.getLogger(__name__)


class GoogleDriveStorageBackend(StorageBackend):
    """Read a reference mount's files from Google Drive by storage key, which is the Drive file id.

    ``backend_config["credential"]`` is the public id of the mount's Google
    credential; each read loads it afresh, so a renewed token is used at once.
    Writes, deletes and public URLs are refused: bytes stay in Drive and are
    served through Angee's proxy.
    """

    writable: ClassVar[bool] = False

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the credential reference without reading it."""

        super().__init__(backend_config=backend_config)
        self.credential_id = str(self.backend_config.get("credential") or "").strip()
        if not self.credential_id:
            raise ImproperlyConfigured("GoogleDriveStorageBackend requires backend_config['credential'].")

    def _open(self, name: str, mode: str = "rb") -> BinaryIO:
        """Return a reader of one Drive file's bytes."""

        if mode not in {"r", "rb"}:
            raise OSError("Google Drive storage is read-only")
        return self._client().open_media(name)

    def _save(self, name: str, content: Any) -> str:
        """Refuse writes to Drive-owned bytes."""

        del name, content
        raise OSError("Google Drive storage is read-only")

    def delete(self, name: str) -> None:
        """Refuse direct deletion of Drive-owned bytes."""

        del name
        raise OSError("Google Drive storage is read-only")

    def exists(self, name: str) -> bool:
        """Return whether the Drive file exists and is not in Drive's trash."""

        try:
            return not self._client().get(name).trashed
        except FileNotFoundError:
            return False

    def size(self, name: str) -> int:
        """Return the Drive file's size in bytes."""

        return self._client().get(name).size

    def url(self, name: str | None) -> str:
        """Refuse public URLs; Drive bytes are served through Angee's proxy."""

        del name
        raise ValueError("Google Drive storage is not URL-accessible")

    def presigned_get(self, key: str, *, expires_in: int) -> str | None:
        """Use the authenticated Angee proxy for every download."""

        del key, expires_in
        return None

    def discard(self, key: str, *, context: str) -> None:
        """Leave Drive-owned bytes untouched when a storage row is purged."""

        logger.info("storage.%s: retained Google Drive object %s", context, key)

    def _client(self) -> GoogleDriveClient:
        """Return a Drive client reading with the configured credential, loaded now."""

        credential_model = apps.get_model("integrate", "Credential")
        rows = credential_model.objects.sudo(reason="storage_integrate_gdrive.storage.credential")
        credential = rows.select_related("oauth_client").from_public_id(self.credential_id)
        if credential is None:
            raise OSError("The Google account of this Drive storage is no longer connected.")
        return GoogleDriveMountBackend.client_for(credential)
