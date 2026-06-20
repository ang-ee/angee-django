"""Django config for Angee's storage addon."""

from __future__ import annotations

from django.apps import AppConfig


class StorageConfig(AppConfig):
    """Source app manifest for the Angee file domain.

    The addon owns credentialed storage backends, addressable drives, folder
    trees, content-addressed file rows, and polymorphic attachments — nothing
    else. Renditions, virus scanning, extraction, and search are downstream
    addons that attach through ``FileAttachment`` or the ``file_finalized``
    signal.
    """

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.storage"
    label = "storage"
    depends_on = (
        "angee.iam",
        "angee.resources",
        "angee.graphql",
        "django.contrib.contenttypes",
    )
    schemas = "schema.schemas"
    permissions = "permissions.zed"

    resources = {
        "master": ({"path": "resources/master/010_storage.mimetype.yaml", "adopt": "mime_type"},),
        "install": (
            {"path": "resources/install/010_storage.backend.yaml", "adopt": "slug"},
            {"path": "resources/install/020_storage.drive.yaml", "adopt": "slug"},
        ),
    }
    """MIME taxonomy (master, adopted by mime type) plus the default local backend and drive."""

    def ready(self) -> None:
        """Wire storage-owned signal receivers after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the user model exists.
        from angee.storage import signals

        signals.connect()
