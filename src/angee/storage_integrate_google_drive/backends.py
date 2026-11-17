"""Read-only storage backend serving a reference Google Drive mount's bytes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, BinaryIO

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.storage_integrate.backends import ExternalStorageBackend
from angee.storage_integrate.models import MountMode
from angee.storage_integrate_google_drive.client import DriveFileError, GoogleDriveClient, GoogleDriveError
from angee.storage_integrate_google_drive.mounts import GoogleDriveMountBackend


class GoogleDriveStorageBackend(ExternalStorageBackend):
    """Read a reference mount's files from Google Drive by storage key, which is the Drive file id.

    ``backend_config["root"]`` names the mounted Drive folder; it holds no
    credential. Each read finds the one reference Mount rooted there and serves
    only while that Mount is connected and this backend's configuration is the
    one its drive is stored on, reading with the Mount's own credential as it is
    now. A second backend row configured alike would make the binding ambiguous,
    so both refuse rather than lend the Mount's access. Every refusal is an
    ``OSError``. The bytes stay in Drive (:class:`ExternalStorageBackend`).
    """

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the mounted folder without reading anything."""

        super().__init__(backend_config=backend_config)
        self.root = str(self.backend_config.get("root") or "").strip()
        if not self.root:
            raise ImproperlyConfigured("GoogleDriveStorageBackend requires backend_config['root'].")

    def open_external(self, name: str) -> BinaryIO:
        """Open one indexed Drive file now: refuse it trashed in Drive or changed since it was indexed."""

        mount, client = self._mount_client()
        try:
            item = client.get(name)
            if item.trashed:
                raise DriveFileError("This file is in the Google Drive trash.")
            indexed = self._indexed_hash(mount, name)
            if item.sha256 and indexed and item.sha256.lower() != indexed:
                raise DriveFileError("This Google Drive file changed since the mount last synced.")
            return client.open_media(name, eager=True)
        except GoogleDriveError as error:
            raise DriveFileError(error.public_message) from error

    def exists(self, name: str) -> bool:
        """Return whether the Drive file exists and is not in Drive's trash."""

        _mount, client = self._mount_client()
        try:
            return not client.get(name).trashed
        except FileNotFoundError:
            return False
        except GoogleDriveError as error:
            raise DriveFileError(error.public_message) from error

    def size(self, name: str) -> int:
        """Return the Drive file's size in bytes."""

        _mount, client = self._mount_client()
        try:
            return client.get(name).size
        except GoogleDriveError as error:
            raise DriveFileError(error.public_message) from error

    def _mount_client(self) -> tuple[Any, GoogleDriveClient]:
        """Return the connected reference Mount this backend serves and a client on its credential, or refuse."""

        mount_model = apps.get_model("storage_integrate", "Mount")
        backend_model = apps.get_model("storage", "Backend")
        with system_context(reason="storage_integrate_google_drive.storage.mount"):
            mounts = list(
                mount_model.objects.for_roots((self.root,))
                .filter(backend_class=GoogleDriveMountBackend.key, mode=MountMode.REFERENCE)
                .select_related("drive__backend")[:2]
            )
            # The storage owner hands a backend no handle on its own row, so the
            # row is the one google_drive backend configured for this root.
            rows = list(
                backend_model.objects.filter(
                    backend_class=GoogleDriveMountBackend.key, backend_config__root=self.root
                ).values_list("pk", "backend_config")[:2]
            )
            mount = mounts[0] if len(mounts) == 1 else None
            bound = (
                mount is not None
                and len(rows) == 1
                and rows[0] == (mount.drive.backend_id, self.backend_config)
            )
            if not bound:
                raise DriveFileError("No Google Drive mount is served by this storage.")
            assert mount is not None  # bound requires it.
            if mount.lifecycle != IntegrationLifecycle.CONNECTED:
                raise DriveFileError("The Google Drive mount of this storage is not connected.")
            credential = mount.load_connection_credential()
        if credential is None:
            raise DriveFileError("The Google Drive mount of this storage has no account.")
        return mount, GoogleDriveClient(credential)

    @staticmethod
    def _indexed_hash(mount: Any, name: str) -> str:
        """Return the content hash the mount indexed for ``name``, or ``""``."""

        file_model = apps.get_model("storage", "File")
        with system_context(reason="storage_integrate_google_drive.storage.indexed_hash"):
            digest = (
                file_model.objects.filter(drive_id=mount.drive_id, storage_path=name)
                .values_list("content_hash", flat=True)
                .first()
            )
        return str(digest or "")
