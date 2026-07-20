"""Local-folder Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils.text import slugify
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.integrate.queue import queue_bridge_sync
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import LocalFolderMountBackend, validate_local_folder_root

_LOCAL_VENDOR_SLUG = "local"


def create_local_folder_mount(
    user: Any,
    *,
    name: str,
    path: str,
    mode: MountMode | str,
) -> Any:
    """Provision and eagerly queue one local-folder Mount."""

    display_name = str(name).strip()
    if not display_name:
        raise ValidationError({"name": "A mount name is required."})
    try:
        root = validate_local_folder_root(path)
    except ValidationError as error:
        if hasattr(error, "error_dict"):
            raise
        raise ValidationError({"path": error.messages}) from error
    try:
        mount_mode = MountMode(str(getattr(mode, "value", mode)).strip().lower())
    except ValueError as error:
        raise ValidationError({"mode": f"Unsupported mount mode: {mode}"}) from error

    backend_model = apps.get_model("storage", "Backend")
    drive_model = apps.get_model("storage", "Drive")
    mount_model = apps.get_model("storage_integrate", "Mount")
    mount_config = dict(root=str(root))
    with system_context(reason="storage_integrate.connect"), transaction.atomic():
        if mount_model.objects.filter(config__root=str(root)).exists():
            raise ValidationError({"path": "This local folder is already mounted."})
        slug = _available_mount_slug(
            display_name,
            backend_model=backend_model,
            drive_model=drive_model,
        )
        drive_slug = f"mount-{slug}"
        if mount_mode == MountMode.REFERENCE:
            mount_backend = mount_model(
                backend_class=LocalFolderMountBackend.key,
                config=mount_config,
            ).backend
            storage_backend_key, storage_backend_config = mount_backend.storage_backend_spec()
            storage_backend = backend_model.objects.create(
                slug=drive_slug,
                label=f"{display_name} (external)",
                backend_class=storage_backend_key,
                backend_config=storage_backend_config,
                created_by_id=user.pk,
            )
            prefix = ""
        else:
            storage_backend = _default_drive(drive_model).backend
            prefix = f"mounts/{slug}"
        drive = drive_model.objects.create(
            backend=storage_backend,
            slug=drive_slug,
            name=display_name,
            prefix=prefix,
            created_by_id=user.pk,
        )
        mount = mount_model.objects.create(
            vendor=_local_vendor(),
            owner=user,
            display_name=display_name,
            backend_class=LocalFolderMountBackend.key,
            drive=drive,
            mode=mount_mode,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            config=mount_config,
            created_by_id=user.pk,
        )
        mount.connect()
    queue_bridge_sync(mount)
    return mount


def _available_mount_slug(name: str, *, backend_model: Any, drive_model: Any) -> str:
    """Return a slug whose mount-prefixed backend and drive ids are unused."""

    base = slugify(name) or "folder"
    suffix = 1
    candidate = base
    while backend_model.objects.filter(slug=f"mount-{candidate}").exists() or drive_model.objects.filter(
        slug=f"mount-{candidate}"
    ).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _default_drive(drive_model: Any) -> Any:
    """Return the configured managed drive, failing clearly on resource drift."""

    try:
        return drive_model.objects.select_related("backend").get(
            slug=str(settings.ANGEE_STORAGE_DEFAULT_DRIVE)
        )
    except drive_model.DoesNotExist as error:
        raise ImproperlyConfigured("The configured default storage drive is missing.") from error


def _local_vendor() -> Any:
    """Return the addon-seeded local vendor row."""

    vendor_model = apps.get_model("integrate", "Vendor")
    try:
        return vendor_model.objects.get(slug=_LOCAL_VENDOR_SLUG)
    except vendor_model.DoesNotExist as error:
        raise ImproperlyConfigured(
            "Local vendor is missing. Load storage_integrate resources before connecting mounts."
        ) from error
