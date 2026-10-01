"""Resource source classes selected by manifest keys.

The resources addon owns local paths; integrate contributes networked URLs through
``ANGEE_RESOURCE_SOURCE_CLASSES`` without adding a network dependency here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.utils._os import safe_join

from angee.base.impl import ImplBase, impl_registry

if TYPE_CHECKING:
    from angee.resources.entries import ResourceEntry


class ResourceSource(ImplBase):
    """Normalize a manifest source and materialize its entry as a local file."""

    registry_setting = "ANGEE_RESOURCE_SOURCE_CLASSES"

    @classmethod
    def registered_keys(cls) -> frozenset[str]:
        """Return source keys understood by this composed host."""

        return frozenset(impl_registry(cls.registry_setting))

    @classmethod
    def normalize(cls, app_config: AppConfig, value: object) -> str:
        """Normalize the source value for durable storage."""

        raise NotImplementedError

    @classmethod
    def materialize_entry(cls, entry: ResourceEntry) -> Path:
        """Materialize one entry as a local file."""

        raise NotImplementedError


class PathSource(ResourceSource):
    """Addon-relative local files."""

    key = "path"

    @classmethod
    def normalize(cls, app_config: AppConfig, value: object) -> str:
        """Return one safe path relative to the addon."""

        raw = str(value)
        if not raw:
            raise ImproperlyConfigured("Manifest path must not be empty")
        try:
            safe_join(app_config.path, raw)
        except SuspiciousFileOperation as error:
            raise ImproperlyConfigured(f"Manifest path {raw!r} must be relative and stay inside the addon") from error
        return raw

    @classmethod
    def materialize_entry(cls, entry: ResourceEntry) -> Path:
        """Resolve an addon-relative file."""

        return Path(entry.addon.path) / entry.source_value
