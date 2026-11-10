"""Local-folder Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError

from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import LocalFolderMountBackend, validate_local_folder_root


def create_local_folder_mount(user: Any, *, name: str, path: str, mode: MountMode) -> Any:
    """Validate and provision one local-folder Mount."""

    try:
        root = validate_local_folder_root(path)
    except ValidationError as error:
        # Django ValidationError has error_dict only for field-keyed errors.
        if hasattr(error, "error_dict"):
            raise
        raise ValidationError({"path": error.messages}) from error
    return apps.get_model("storage_integrate", "Mount").objects.provision(
        user,
        name=name,
        root=root,
        mode=mode,
        backend_class=LocalFolderMountBackend.key,
        already_mounted_message="This local folder is already mounted.",
        slug_default="folder",
        reason="storage_integrate.connect",
    )
