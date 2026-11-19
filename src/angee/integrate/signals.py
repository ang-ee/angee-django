"""Committed connection discovery events contributed to by capability addons, and integrate's own receivers."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models import Model
from django.db.models.signals import post_delete
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
        post_delete,
        release_derived_credential,
        applies=holds_the_integration_row,
        dispatch_uid="angee.integrate.release_derived_credential",
    )


def release_derived_credential(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Remove the credential a deleted integration held as derived, unless another one still uses it.

    Discovery stored that material for this integration alone
    (``CredentialManager.store_derived``), which refuses to overwrite a row no
    integration holds as derived; left behind, it would block the next discovery
    of that name. It goes in the delete's transaction, after the integration row,
    the integrate lock order; its source grant stays.
    """

    if instance.discovery_credential_id in (None, instance.credential_id):
        return
    with system_context(reason="integrate.integration.release_derived_credential"):
        apps.get_model("integrate", "Credential").objects.delete_unattached_derived(instance.credential_id)
