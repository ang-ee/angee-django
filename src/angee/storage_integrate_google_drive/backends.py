"""Read-only storage backend serving a reference Google Drive mount's bytes."""

from __future__ import annotations

from typing import Any, BinaryIO

from django.apps import apps
from rebac import system_context

from angee.integrate.errors import IntegrationError
from angee.integrate.models import IntegrationLifecycle
from angee.storage_integrate.backends import ExternalStorageBackend
from angee.storage_integrate.models import MountMode
from angee.storage_integrate_google_drive.client import DriveFileError, GoogleDriveClient
from angee.storage_integrate_google_drive.mounts import GoogleDriveMountBackend


class GoogleDriveStorageBackend(ExternalStorageBackend):
    """Read a reference mount's files from Google Drive by storage key, which is the Drive file id.

    It holds no configuration and no credential. It serves the one reference
    Mount whose drive is stored on this backend's own row (``backend_row``, which
    ``Backend.storage`` passes), only while that Mount is connected, reading with
    the Mount's own credential as it is now; any other row reaches nothing.
    It serves only files a live row of that drive indexes. Refusals are
    ``OSError``s: unindexed, missing or trashed is ``FileNotFoundError``, a
    refused binding ``PermissionError``, and any Drive or integration refusal,
    or a file changed since the last sync, :class:`DriveFileError`. The bytes stay in Drive
    (:class:`ExternalStorageBackend`).
    """

    def open_external(self, name: str) -> BinaryIO:
        """Open one indexed Drive file now: refuse it unindexed, trashed in Drive, or changed since it was indexed."""

        mount, client = self._mount_client()
        content_hash, size_bytes = self._indexed(mount, name)
        try:
            item = client.get(name)
            if item.trashed:
                raise FileNotFoundError("This file is in the Google Drive trash.")
            if item.size != size_bytes or (item.sha256 and item.sha256.lower() != content_hash):
                raise DriveFileError("This Google Drive file changed since the last sync.")
            return client.open_media(name, eager=True)
        except IntegrationError as error:
            raise DriveFileError(error.public_message) from error

    def exists(self, name: str) -> bool:
        """Return whether the file is indexed here and still in Drive, outside its trash."""

        mount, client = self._mount_client()
        try:
            self._indexed(mount, name)
            return not client.get(name).trashed
        except FileNotFoundError:
            return False
        except IntegrationError as error:
            raise DriveFileError(error.public_message) from error

    def size(self, name: str) -> int:
        """Return the indexed Drive file's size in bytes."""

        mount, client = self._mount_client()
        self._indexed(mount, name)
        try:
            return client.get(name).size
        except IntegrationError as error:
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
    def _indexed(mount: Any, name: str) -> tuple[str, int]:
        """Return the content hash and size of the live row the mount indexed under ``name``.

        Only an indexed file is served: a Drive id no live row of the mount's
        drive carries is ``FileNotFoundError``, whatever Drive holds under it.
        """

        file_model = apps.get_model("storage", "File")
        with system_context(reason="storage_integrate_google_drive.storage.indexed"):
            indexed = (
                file_model.objects.filter(drive_id=mount.drive_id, storage_path=name)
                .untrashed()
                .values_list("content_hash", "size_bytes")
                .first()
            )
        if indexed is None:
            raise FileNotFoundError("This Google Drive file is not indexed by its mount.")
        return str(indexed[0]), int(indexed[1])
