"""Native backend owners for storage."""

from __future__ import annotations

import logging
import os
import posixpath
from collections import OrderedDict
from pathlib import Path
from typing import Any, ClassVar

from django.db import models
from rebac import (
    system_context,
)

from angee.base.fields.implementation import ImplClassField
from angee.base.models.audit import AuditMixin
from angee.base.models.trash import ArchiveMixin
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import ItemOwnershipMixin, OwnerMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.storage.backends import StorageBackend
from angee.storage.managers import (
    DriveManager,
    StorageMasterManager,
)
from angee.storage.uploads import (
    _object_key_leaf,
)

logger = logging.getLogger(__name__)

_STORAGE_CACHE_MAX_SIZE = 128


class Backend(AuditMixin, ArchiveMixin, ProtectedDataModel, concrete=True):
    """Credentialed storage backend instance.

    One row names a :class:`~angee.storage.backends.StorageBackend` subclass by
    a key in ``ANGEE_STORAGE_BACKEND_CLASSES`` and carries its constructor
    config. Many drives can share one backend; credentials are written once,
    here, never on the drive.
    """

    sqid_prefix = "bkd_"
    slug = models.SlugField(unique=True)
    label = models.CharField(max_length=200)
    backend_class = ImplClassField(StorageBackend)
    backend_config = models.JSONField(default=dict, blank=True)

    objects = StorageMasterManager()

    _storage_cache: ClassVar[OrderedDict[tuple[Any, ...], StorageBackend]] = OrderedDict()
    """Resolved backend instances keyed by database, row, selected class and resolved config."""

    class Meta:
        """Django model options for storage backends."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "storage/backend"

    def __str__(self) -> str:
        """Return the operator-facing backend label."""

        return self.label or self.slug

    def resolved_config(self) -> dict[str, Any]:
        """Return ``backend_config`` with ``{"env": "VAR"}`` placeholders expanded.

        Credentials stay out of the database row; env references resolve
        against the process environment each time an instance is built.
        """

        def expand(value: Any) -> Any:
            if isinstance(value, dict):
                if set(value) == {"env"} and isinstance(value["env"], str):
                    return os.environ.get(value["env"], "")
                return {key: expand(item) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item) for item in value]
            return value

        return {key: expand(item) for key, item in dict(self.backend_config or {}).items()}

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend instance, cached per database, row, implementation and config.

        The cache key includes the resolved config, so rotating an env-backed
        credential takes effect on the next lookup without a worker restart.
        """

        config = self.resolved_config()
        backend_class = self.get_backend_class_impl()
        key = (self._state.db, self.pk, backend_class, _frozen(config))
        cached = self._storage_cache.pop(key, None)
        if cached is not None:
            self._storage_cache[key] = cached
            return cached
        instance = backend_class(backend_config=config)
        self._storage_cache[key] = instance
        while len(self._storage_cache) > _STORAGE_CACHE_MAX_SIZE:
            self._storage_cache.popitem(last=False)
        return instance


class Drive(OwnerMixin, ItemOwnershipMixin, ArchiveMixin, ProtectedDataModel, concrete=True):
    """Addressable storage volume on top of a backend.

    Object keys live under ``{prefix}/…`` inside the parent backend's
    namespace, so two drives can share one bucket as long as their prefixes
    differ. Drive rows are the unit of access control: per-row ``editor`` /
    ``viewer`` grants (see ``permissions.zed``) scope every folder and file
    underneath.
    """

    rebac_grantable = {"editor": "share", "viewer": "share"}

    sqid_prefix = "drv_"
    backend = models.ForeignKey(
        "storage.Backend",
        on_delete=models.PROTECT,
        related_name="drives",
    )
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    prefix = models.CharField(max_length=512, blank=True)

    objects = DriveManager()
    unscoped_objects = AngeeHistoryUnscopedManager()

    class Meta:
        """Django model options for drives."""

        abstract = True
        base_manager_name = "unscoped_objects"
        ordering = ("slug",)
        rebac_resource_type = "storage/drive"
        constraints = (
            # Two drives on one backend must not share a key space — purge in
            # one drive could delete bytes a row in the other references.
            models.UniqueConstraint(
                fields=("backend", "prefix"),
                name="uniq_storage_drive_backend_prefix",
            ),
        )

    def __str__(self) -> str:
        """Return the drive display name."""

        return self.name or self.slug

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend instance for this drive.

        Backend rows are admin-gated infrastructure; the fetch runs elevated
        so any actor allowed to use the drive can perform storage IO without
        read access to the backend row itself. Cached relations need no
        additional elevation or audit event.
        """

        if type(self).backend.field.is_cached(self):
            return self.backend.storage
        with system_context(reason="storage.drive.storage"):
            return self.backend.storage

    def local_file_path(self, filename: str) -> Path | None:
        """Resolve the first readable live file of this name to its local path.

        Deterministic storage-path order chooses among repeated leaf names.
        Object-store files and missing local bytes have no on-disk location.
        """
        actor = self.require_access("read")
        rows = (
            self.files.with_actor(actor)
            if actor is not None
            else self.files.system_context(reason="storage.drive.local_file_path")
        )
        file = rows.filter(filename=filename, is_trashed=False).order_by("storage_path", "pk").first()
        path = file.local_path() if file is not None else None
        return path if path is not None and path.is_file() else None

    def object_key(self, content_hash: str, filename: str) -> str:
        """Return the deterministic backend key for one digest and filename.

        ``{prefix}/{hash[:2]}/{hash[2:4]}/{hash}/{safe_filename}`` — the
        two-level digest sharding keeps any single directory small.
        """

        digest = str(content_hash).lower()
        parts = [digest[:2], digest[2:4], digest, _object_key_leaf(filename)]
        prefix = str(self.prefix or "").strip("/")
        if prefix:
            parts.insert(0, prefix)
        return posixpath.join(*parts)


def _frozen(value: Any) -> Any:
    """Return ``value`` recursively converted to a hashable cache key."""

    if isinstance(value, dict):
        return tuple(sorted((key, _frozen(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    return value
