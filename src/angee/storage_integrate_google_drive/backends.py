"""Read-only storage backend serving a reference Google Drive mount's bytes."""

from __future__ import annotations

from typing import Any, BinaryIO

from django.apps import apps
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.storage_integrate.backends import ExternalStorageBackend
from angee.storage_integrate.models import MountMode
from angee.storage_integrate_google_drive.client import DriveFileError, GoogleDriveClient, GoogleDriveError
from angee.storage_integrate_google_drive.mounts import GoogleDriveMountBackend


class GoogleDriveStorageBackend(ExternalStorageBackend):
    """Read a reference mount's files from Google Drive by storage key, which is the Drive file id.

    It holds no configuration and no credential. It serves the one reference
    Mount whose drive is stored on this backend's own row (``backend_row``, which
    ``Backend.storage`` passes), only while that Mount is connected, reading with
    the Mount's own credential as it is now; any other row reaches nothing.
    Refusals are ``OSError``s: missing or trashed is ``FileNotFoundError``, a
    refused binding ``PermissionError``, and a Drive refusal or a file changed
    since the last sync :class:`DriveFileError`. The bytes stay in Drive
    (:class:`ExternalStorageBackend`).
    """

    def open_external(self, name: str) -> BinaryIO:
        """Open one indexed Drive file now: refuse it trashed in Drive or changed since it was indexed."""

        mount, client = self._mount_client()
        try:
            item = client.get(name)
            if item.trashed:
                raise FileNotFoundError("This file is in the Google Drive trash.")
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
        """Return the connected reference Mount stored on this row and a client on its credential, or refuse."""

        if self.backend_row is None:
            raise PermissionError("This Google Drive storage is not bound to a storage backend row.")
        mount_model = apps.get_model("storage_integrate", "Mount")
        with system_context(reason="storage_integrate_google_drive.storage.mount"):
            mount = (
                mount_model.objects.filter(
                    drive__backend_id=self.backend_row.pk,
                    backend_class=GoogleDriveMountBackend.key,
                    mode=MountMode.REFERENCE,
                )
                .order_by("pk")
                .first()
            )
            if mount is None:
                raise PermissionError("No Google Drive mount is stored on this storage backend.")
            if mount.lifecycle != IntegrationLifecycle.CONNECTED:
                raise PermissionError("The Google Drive mount of this storage is not connected.")
            credential = mount.load_connection_credential()
        if credential is None:
            raise PermissionError("The Google Drive mount of this storage has no account.")
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
