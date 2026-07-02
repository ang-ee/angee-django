"""Registry of resource source types.

A resource entry names its source by a manifest key — ``path`` for a local file,
``url`` for a remote one. The ``resources`` addon owns the source-type seam but
ships only the local ``path`` source; an addon that owns outbound networking,
``angee.integrate``, contributes the ``url`` source through settings, so
``resources`` itself stays free of any network concern. Each source declares how
to normalize its manifest value and how to materialize an entry to a local file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.utils._os import safe_join
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from angee.resources.entries import ResourceEntry


@dataclass(frozen=True, slots=True)
class ResourceSource:
    """One resource source type, keyed by its manifest key (``path``/``url``)."""

    key: str
    """The manifest key that selects this source in a resource declaration."""

    normalize: Callable[[AppConfig, Any], str]
    """Return the stored source value from one raw manifest value."""

    materialize: Callable[[ResourceEntry], Path]
    """Return the local file path for one entry, fetching/resolving as needed."""


_DEFAULT_SOURCE_CLASSES = {
    "path": "angee.resources.sources.path_source",
}


def source_registry() -> dict[str, ResourceSource]:
    """Return resource source types declared by settings."""

    registry: dict[str, ResourceSource] = {}
    for key, dotted_path in _source_class_paths().items():
        source = _load_source(str(key), dotted_path)
        if source.key in registry:
            raise ImproperlyConfigured(f"resource source {source.key!r} is configured more than once")
        registry[source.key] = source
    return registry


def source_keys() -> frozenset[str]:
    """Return every configured source key."""

    return frozenset(source_registry())


def get_source(key: str) -> ResourceSource:
    """Return the configured source for ``key``, or raise with an install hint."""

    registry = source_registry()
    try:
        return registry[key]
    except KeyError as error:
        raise ImproperlyConfigured(
            f"resource source {key!r} is not registered; the addon that provides it "
            f"(e.g. angee.integrate for 'url') must be installed"
        ) from error


def path_source() -> ResourceSource:
    """Return the built-in local-file resource source."""

    return ResourceSource(key="path", normalize=normalize_path, materialize=materialize_path)


def normalize_path(app_config: AppConfig, value: object) -> str:
    """Return one safe path relative to ``app_config.path``."""

    raw = str(value)
    if not raw:
        raise ImproperlyConfigured("Manifest path must not be empty")
    try:
        safe_join(app_config.path, raw)
    except SuspiciousFileOperation as error:
        raise ImproperlyConfigured(f"Manifest path {raw!r} must be relative and stay inside the addon") from error
    return raw


def materialize_path(entry: ResourceEntry) -> Path:
    """Materialize a local ``path`` source to its addon-relative file."""

    return Path(entry.addon.path) / entry.source_value


def _source_class_paths() -> dict[str, str]:
    """Return configured resource source factories keyed by manifest field."""

    configured = getattr(settings, "ANGEE_RESOURCE_SOURCE_CLASSES", {})
    if not isinstance(configured, dict):
        raise ImproperlyConfigured("ANGEE_RESOURCE_SOURCE_CLASSES must be a mapping")
    return {**_DEFAULT_SOURCE_CLASSES, **{str(key): str(value) for key, value in configured.items()}}


def _load_source(key: str, dotted_path: str) -> ResourceSource:
    """Load one configured resource source factory."""

    factory = import_string(dotted_path)
    source = factory()
    if not isinstance(source, ResourceSource):
        raise ImproperlyConfigured(f"resource source factory {dotted_path!r} must return ResourceSource")
    if source.key != key:
        raise ImproperlyConfigured(
            f"resource source factory {dotted_path!r} returned key {source.key!r}, expected {key!r}"
        )
    return source
