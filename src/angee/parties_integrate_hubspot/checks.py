"""System checks of the HubSpot directory addon."""

from __future__ import annotations

from typing import Any

from django.core import checks

from angee.base.implementations import check_impl_registry, resolve_all_impl_classes
from angee.parties_integrate_hubspot.backends import HubSpotObjectMapping


def check_object_mappings(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Check ``ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES``: each mapping claims its key and reads another type than contacts.

    The registry's own check comes first: every entry resolves to a
    :class:`HubSpotObjectMapping` that claims the key it is registered under, so
    no key maps twice. Then a mapping naming no object type, or contacts, which a
    directory reads itself and a ``DirectoryMapping`` maps
    (``parties_integrate_hubspot.E001``), fails at startup rather than at its first
    sync.
    """

    del app_configs, kwargs
    errors = check_impl_registry(HubSpotObjectMapping)
    if errors:
        return errors
    return [
        checks.Error(
            f"HubSpot object mapping {mapping.key!r} must name a HubSpot object type other than contacts, which a "
            f"directory reads itself and a directory mapping maps; it names {mapping.object_type!r}.",
            obj=mapping,
            id="parties_integrate_hubspot.E001",
        )
        for mapping in resolve_all_impl_classes(HubSpotObjectMapping)
        if mapping.object_type in ("", "contacts")
    ]
