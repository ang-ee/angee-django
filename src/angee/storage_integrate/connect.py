"""Local-folder Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import LocalFolderMountBackend


def create_local_folder_mount(
    user: Any,
    *,
    name: str,
    path: str,
    mode: MountMode,
    copy_backend: Any | None = None,
) -> Any:
    """Provision one local-folder Mount; the backend canonicalizes and validates ``path``."""

    return apps.get_model("storage_integrate", "Mount").objects.provision(
        user,
        name=name,
        root=path,
        mode=mode,
        backend_class=LocalFolderMountBackend.key,
        already_mounted_message="This local folder is already mounted.",
        slug_default="folder",
        reason="storage_integrate.connect",
        copy_backend=copy_backend,
    )
