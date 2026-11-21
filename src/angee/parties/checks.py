"""Parties' system checks, registered by ``PartiesConfig.ready()``."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from angee.base.implementations import check_impl_registry, resolve_all_impl_classes, resolve_impl_class
from angee.integrate.states import StreamDirection
from angee.parties.backends import REFINABLE_FIELDS, DirectoryBackend, DirectoryMapping


def check_directory_mappings(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Check ``ANGEE_DIRECTORY_MAPPING_CLASSES``: each mapping maps a registered, pull-only directory backend.

    The registry's own check comes first: every entry resolves to a
    :class:`DirectoryMapping` that claims the key it is registered under, so no
    key maps twice. Then a mapping naming no registered backend
    (``parties.E001``) or a backend that writes back (``parties.E002``), one
    declaring a statement group the person model has not (``parties.E003``;
    one that states none, because it only refines the contact or writes through
    other owners, declares none), one declaring a group the contact ingest
    states (``parties.E004``: ``Person.contact_aspects``, whose statement the
    mapping would overwrite), one refining a contact field outside
    ``REFINABLE_FIELDS`` (``parties.E005``), or two mappings of one backend
    refining one field (``parties.E006``, rather than the last silently
    winning), fails at startup rather than at its first sync.
    """

    del app_configs, kwargs
    errors = check_impl_registry(DirectoryMapping)
    if errors:
        return errors
    person = apps.get_model("parties", "Person")
    groups = {aspect for model in person.statement_models() for aspect in model.statement_aspects}
    ingested = person.contact_aspects()
    refiners: dict[tuple[str, str], list[str]] = {}
    for mapping in resolve_all_impl_classes(DirectoryMapping):
        for name in mapping.refines:
            refiners.setdefault((mapping.backend, name), []).append(mapping.key)
        if closed := sorted(set(mapping.refines) - set(REFINABLE_FIELDS)):
            errors.append(
                checks.Error(
                    f"Directory mapping {mapping.key!r} refines {closed!r}; a mapping refines only the contact "
                    f"fields {list(REFINABLE_FIELDS)!r}.",
                    obj=mapping,
                    id="parties.E005",
                )
            )
        if unknown := sorted(set(mapping.aspects) - groups):
            errors.append(
                checks.Error(
                    f"Directory mapping {mapping.key!r} declares {unknown!r}, statement groups the person has not; "
                    f"it has {sorted(groups)!r}.",
                    obj=mapping,
                    id="parties.E003",
                )
            )
        if overlap := sorted(set(mapping.aspects) & ingested):
            errors.append(
                checks.Error(
                    f"Directory mapping {mapping.key!r} declares {overlap!r}, groups the contact ingest states; a "
                    "mapping states only groups no contact carries.",
                    obj=mapping,
                    id="parties.E004",
                )
            )
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
    for (backend_key, name), keys in sorted(refiners.items()):
        if len(keys) > 1:
            errors.append(
                checks.Error(
                    f"Directory mappings {sorted(keys)!r} of {backend_key!r} all refine the contact's {name!r}; one "
                    "mapping refines a field.",
                    id="parties.E006",
                )
            )
    return errors
