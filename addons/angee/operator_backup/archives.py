"""Configured Files destination for operator-published archive bytes."""

import stat
from pathlib import Path
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from rebac import system_context

from angee.serialization import canonical_json_sha256


def archive_mount(*, required: bool = True, lock: bool = False) -> Any:
    """Resolve the existing read-only Mount that owns archive byte access."""
    with system_context(reason="operator_backup.archive_mount"):
        model = apps.get_model("storage_integrate", "Mount")
        rows = model.system_queryset(lock=("self", "drive", "drive__backend") if lock else None)
        mount = rows.select_related("drive__backend").filter(
            drive__slug=cast(Any, settings).ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE,
            mode="reference", backend_class="local_folder",
        ).first()
    if mount is None and required:
        raise ImproperlyConfigured(
            "Configure a reference local-folder Mount for the operator backups/archives directory "
            "and set ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE to its drive slug."
        )
    return mount


def archive_mount_signature(mount: Any) -> str:
    """Pin database-owned destination configuration without retaining raw paths."""
    return canonical_json_sha256({
        "mount": str(mount.sqid), "mount_backend": mount.backend_class, "mode": mount.mode,
        "config": mount.config, "drive": str(mount.drive.sqid), "prefix": mount.drive.prefix,
        "backend": str(mount.drive.backend.sqid), "backend_class": mount.drive.backend.backend_class,
        "backend_config": mount.drive.backend.resolved_config(),
    })


def validate_archive_destination(mount: Any, *, stack_root: str) -> Path:
    """Bind the read-only Files destination to the authenticated operator root."""
    backend_class, backend_config = mount.backend.storage_backend_spec()
    if (mount.drive.prefix or mount.drive.backend.backend_class != backend_class
            or mount.drive.backend.resolved_config() != backend_config):
        raise ValidationError("The backup Files destination has drifted from its reference Mount.")
    root = Path(str(backend_config["root"]))
    expected = Path(stack_root) / "backups" / "archives"
    if not Path(stack_root).is_absolute() or root.resolve(strict=True) != expected.resolve(strict=True):
        raise ValidationError("The backup Mount must reference this operator stack's archive directory.")
    return root


def validate_archive_file(root: Path, *, filename: str, size_bytes: int) -> None:
    """Check bounded local metadata; the operator owns completed byte verification."""
    info = (root / filename).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
        raise ValidationError("The completed archive is not a regular file matching the operator receipt size.")
