"""Committed connection discovery events contributed to by capability addons, and integrate's own receivers."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models import Model
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import Signal
from rebac import system_context

from angee.base.signals import connect_for_models

discovery_finished = Signal()
"""Discovery applied successfully: ``sender=type(instance)``, ``instance=integration``."""


def connect() -> None:
    """Remove the credential an integration holds as derived with the integration, on every delete path."""

    from angee.integrate.models import Integration

    def holds_the_integration_row(model: type[Model]) -> bool:
        # The integration row itself, and its proxies: deleting an MTI child deletes that row too.
        return issubclass(model, Integration) and not (model._meta.concrete_model or model)._meta.parents

    connect_for_models(
        pre_delete,
        lock_integration_for_delete,
        applies=holds_the_integration_row,
        dispatch_uid="angee.integrate.lock_integration_for_delete",
    )
    connect_for_models(
        post_delete,
        release_derived_credential,
        applies=holds_the_integration_row,
        dispatch_uid="angee.integrate.release_derived_credential",
    )


_LINKS = ("credential", "discovery_credential", "discovery_generation")
"""The integration columns that say which credential it holds, and how."""


def lock_integration_for_delete(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Take an integration's delete locks on every delete path, and read its credential links from the locked row.

    The deleting instance may have been read before discovery attached derived
    material; :func:`release_derived_credential` decides on what the locked row holds.
    """

    with system_context(reason="integrate.integration.lock_for_delete"):
        if instance.lock_for_delete() is not None:
            instance.refresh_from_db(fields=_LINKS)


def release_derived_credential(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Remove the credential a deleted integration held as derived, unless something else still uses it.

    Discovery stored that material for this integration alone
    (``CredentialManager.store_derived``), and the integration still holds it
    when its source grant was lost (``Integration.derived_credential_id``). It
    goes in the delete's transaction, after the integration row, the integrate
    lock order; the credential is locked before nothing-holds-it is decided
    (``CredentialManager.delete_unattached_derived``), and its source grant stays.
    """

    with system_context(reason="integrate.integration.release_derived_credential"):
        apps.get_model("integrate", "Credential").objects.delete_unattached_derived(instance.derived_credential_id)
