"""Django config for Angee's storage addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class StorageConfig(AppConfig):
    """Source app manifest for the Angee file domain.

    The addon owns credentialed storage backends, addressable drives, folder
    trees, content-addressed file rows, and polymorphic attachments — nothing
    else. Renditions, virus scanning, extraction, and search are downstream
    addons that attach through ``FileAttachment`` or the ``file_finalized``
    signal.
    """

    default = True
    name = "angee.storage"
    requires_apps = ('django.contrib.contenttypes',)

    def ready(self) -> None:
        """Wire storage-owned signal receivers and register the sealed file permissions after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the user model exists.
        from angee.storage import signals
        from angee.storage.checks import check_derived_permissions

        signals.connect()
        checks.register(check_derived_permissions, checks.Tags.security)
