"""Integration implementation descriptors.

An ``Integration`` row stores the registry key for the implementation that owns its
behaviour. Concrete addons contribute subclasses through ``ANGEE_INTEGRATION_IMPLS``;
this base keeps only the shared catalogue/connect metadata and the optional link to a
*related model* — the structured 1:1 data an implementation needs beyond the
``Integration``'s generic ``config`` blob (e.g. a bridge's sync cursor).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from angee.base.impl import ImplBase


class IntegrationImpl(ImplBase):
    """Base descriptor for one row-selected integration implementation."""

    category = "none"
    related_model: str | None = None
    related_create_fields: tuple[str, ...] = ()
    related_create_input_fields: ClassVar[Mapping[str, str]] = {}
    label = "Integration"
    icon = ""
    oauth_client = ""

    def __init__(self, integration: Any, related: Any | None = None) -> None:
        """Bind this implementation to its owning integration and its related row."""

        self.integration = integration
        self.related = related

    @classmethod
    def related_model_class(cls) -> type[models.Model] | None:
        """Return this implementation's related model class, when it declares one."""

        if not cls.related_model:
            return None
        app_label, model_name = cls.related_model.split(".", 1)
        return apps.get_model(app_label, model_name)

    @classmethod
    def related_row(cls, integration: Any) -> Any | None:
        """Return this implementation's 1:1 related row for ``integration``, if present."""

        model = cls.related_model_class()
        if model is None:
            return None
        related_name = f"{model._meta.app_label}_{model._meta.model_name}"
        try:
            return getattr(integration, related_name)
        except ObjectDoesNotExist:
            return None

    @classmethod
    def create_related_row(cls, integration: Any, values: dict[str, Any]) -> Any | None:
        """Create this implementation's 1:1 related row from declared create values."""

        model = cls.related_model_class()
        if model is None:
            return None
        attrs = cls.related_create_values(integration, values)
        return model.objects.create(integration=integration, **attrs)

    @classmethod
    def related_create_values(cls, integration: Any, values: dict[str, Any]) -> dict[str, Any]:
        """Return the subset of create values owned by the related model.

        Listed explicitly (not derived from the model's fields) because input names
        don't always match field names — e.g. the form's ``related_config`` maps to
        the related model's own ``config`` column, distinct from the Integration's.
        """

        del integration
        return {field: values[field] for field in cls.related_create_fields if field in values}

    @classmethod
    def related_create_values_from_input(cls, integration: Any, source: Any, *, unset: Any) -> dict[str, Any]:
        """Return related create values read from an owner-declared input mapping."""

        values: dict[str, Any] = {}
        for field in cls.related_create_fields:
            input_name = cls.related_create_input_fields.get(field, field)
            value = getattr(source, input_name, unset)
            if value not in (None, "", unset):
                values[field] = value
        return cls.related_create_values(integration, values)


class NullIntegrationImpl(IntegrationImpl):
    """Neutral implementation for a draft row with no chosen implementation."""

    key = "none"
    label = "Draft"


class BridgeImpl(IntegrationImpl):
    """Base descriptor for an inbound bridge — it pulls/subscribes to external data.

    Bridges run on a schedule (``run_due_bridges`` over ``Bridge.next_sync_at``) and
    keep their sync state on a ``Bridge`` related model.
    """

    category = "bridge"
    label = "Bridge"
    icon = "plug"


class Client(IntegrationImpl):
    """Base descriptor for an outbound client — it calls out to an external service.

    The counterpart of :class:`BridgeImpl` (which pulls data in): a client sends
    requests to a remote API. The call itself lives on the concrete subclass; this
    base only carries the ``client`` category. A client is often stateless (its
    settings ride the Integration's ``config``) and declares no ``related_model``.
    """

    category = "client"
    label = "Client"
    icon = "send"


class QueuedClient(Client):
    """Base for a client whose work is meant to run asynchronously, with retries.

    The vocabulary for calls too slow or failure-prone to run inline — outbound
    sends, or long-running remote jobs like training / video inference. A concrete
    subclass implements :meth:`run`; ``max_retries``/``retry_backoff_base_seconds``
    declare its retry policy.

    NOTE: no async dispatcher is wired yet. The stack earmarks Celery for queues and
    retries (``docs/stack.md``) but it is not locked, so this base only fixes the
    contract a future Celery (or due-time scanner) layer will drive — it must not be
    relied on for dispatch until that lands. A provider that submits a remote job and
    polls would persist the remote handle on its ``related_model`` and reschedule
    until done.
    """

    max_retries: int = 5
    retry_backoff_base_seconds: int = 10

    def run(self, payload: dict[str, Any]) -> Any:
        """Perform one unit of queued work; implemented by the concrete client."""

        raise NotImplementedError
