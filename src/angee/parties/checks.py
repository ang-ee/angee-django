"""Parties' system checks, registered by ``PartiesConfig.ready()``."""

from __future__ import annotations

from typing import Any

from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from angee.base.implementations import check_impl_registry, resolve_all_impl_classes, resolve_impl_class
from angee.integrate.states import StreamDirection
from angee.parties.backends import DirectoryBackend, DirectoryMapping


def check_directory_mappings(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Check ``ANGEE_DIRECTORY_MAPPING_CLASSES``: each mapping maps a registered, pull-only directory backend.

    The registry's own check comes first: every entry resolves to a
    :class:`DirectoryMapping` that claims the key it is registered under, so no
    key maps twice. Then a mapping naming no registered backend
    (``parties.E001``) or a backend that writes back (``parties.E002``) fails at
    startup rather than at its first sync.
    """

    del app_configs, kwargs
    errors = check_impl_registry(DirectoryMapping)
    if errors:
        return errors
    for mapping in resolve_all_impl_classes(DirectoryMapping):
        try:
            backend = resolve_impl_class(DirectoryBackend, mapping.backend)
        except (ImportError, ImproperlyConfigured) as error:
            errors.append(
                checks.Error(
                    f"Directory mapping {mapping.key!r} names no directory backend: {error}",
                    obj=mapping,
                    id="parties.E001",
                )
            )
            continue
        if backend.direction != StreamDirection.PULL:
            errors.append(
                checks.Error(
                    f"Directory mapping {mapping.key!r} maps {mapping.backend!r}, a directory backend that writes "
                    "back; mappings fill a consumer's columns from pull-only directories only.",
                    obj=mapping,
                    id="parties.E002",
                )
            )
    return errors
