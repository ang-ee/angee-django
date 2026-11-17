"""Google Drive Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.storage_integrate.models import MountMode
from angee.storage_integrate_gdrive.mounts import GoogleDriveMountBackend


def create_google_drive_mount(
    user: Any,
    *,
    name: str,
    credential: Any,
    path: str,
    mode: MountMode,
    copy_backend: Any | None = None,
) -> Any:
    """Provision one Mount of a Google Drive folder, My Drive or a shared drive, read with ``credential``.

    ``path`` is the folder's browse token (its Drive id). Reference mode serves
    the files from Drive read-only; copy mode copies their bytes into
    ``copy_backend`` or the default drive's backend.
    """

    return apps.get_model("storage_integrate", "Mount").objects.provision(
        user,
        name=name,
        root=path,
        mode=mode,
        backend_class=GoogleDriveMountBackend.key,
        already_mounted_message="This Google Drive folder is already mounted.",
        slug_default="google-drive",
        reason="storage_integrate_gdrive.connect",
        credential=credential,
        copy_backend=copy_backend,
    )
