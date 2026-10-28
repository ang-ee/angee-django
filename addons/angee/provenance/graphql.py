"""Optional provenance adapters over native GraphQL write and result contracts."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

import strawberry
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, current_actor
from rebac.resources import model_resource_type
from strawberry.types import ExecutionResult
from strawberry_django_hasura import WriteBackend

from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.publishing import publish_bulk_update
from angee.graphql.views import result_processed
from angee.base.permissions import require_rebac_queryset
from angee.provenance.context import (
    changeset,
    changeset_model,
    changeset_model_label,
    mark_changeset_failed,
    require_changeset,
)
from angee.base.signals import bulk_updated, connect_for_models


def _claimed_create(
    model: type[models.Model],
    idempotency_key: str | None,
    *,
    values: Any,
    insert: Callable[[], Any],
) -> Any:
    """Insert once for one actor-scoped resource and replay its claimed row.

    Without a key the insert runs as the backend wrote it: a custom backend owns
    its own transaction, as upstream calls it.
    """

    if idempotency_key is None:
        return insert()
    with changeset(fresh=True), transaction.atomic():
        actor = current_actor()
        if not model_resource_type(model) or actor is None:
            raise ImproperlyConfigured("Idempotent resources require an actor-scoped queryset.")
        replay = changeset_model().objects.claim_created(
            f"graphql.create:{model._meta.label_lower}:{actor}",
            idempotency_key,
            values=values, model=model,
            unavailable=ValidationError(
                {"idempotency_key": "The claimed creation result is unavailable."},
            ),
        )
        if replay is not None:
            instance = require_rebac_queryset(model).with_actor(actor).filter(pk=replay.pk).first()
            if instance is None:
                raise PermissionDenied("The claimed creation result is not readable.")
            return instance
        instance = insert()
        require_changeset().record_created(instance)
        return instance


class ClaimingWriteBackend:
    """Decorate native writes with actor-scoped creation claims and replay."""

    def __init__(self, model: type[models.Model], backend: WriteBackend) -> None:
        self.model = model
        self.backend = backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backend, name)

    def create(
        self, info: strawberry.Info, data: dict[str, Any], *, idempotency_key: str | None = None,
    ) -> Any:
        values = self.backend.creation_values(data) if isinstance(self.backend, AngeeHasuraWriteBackend) else data
        return _claimed_create(
            self.model, idempotency_key, values=values,
            insert=lambda: self.backend.create(info, data),
        )


def write_backend(model: type[models.Model], backend: WriteBackend) -> tuple[WriteBackend, dict[str, Any]]:
    """Add the native root argument only alongside the backend that implements it."""

    return ClaimingWriteBackend(model, backend), {"idempotency_key": str | None}


def _mark_failed(sender: object, *, result: ExecutionResult, **kwargs: Any) -> None:
    if result.errors:
        mark_changeset_failed()


def setup_graphql() -> None:
    """Bind result failures and closure publication without changing storage behavior."""

    result_processed.connect(_mark_failed, dispatch_uid="provenance.graphql_result")
    connect_for_models(
        bulk_updated, partial(publish_bulk_update, broadcast=False),
        applies=lambda model: model._meta.label_lower == changeset_model_label().lower(),
        dispatch_uid="provenance.changeset_closed",
    )
