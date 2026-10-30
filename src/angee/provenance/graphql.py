"""Optional provenance adapters over native GraphQL write and result contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from functools import partial
from typing import Any

import strawberry
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, current_actor
from rebac.resources import model_resource_type
from strawberry.extensions import SchemaExtension
from strawberry_django_hasura import WriteBackend

from angee.base.permissions import require_rebac_queryset
from angee.base.signals import bulk_updated, connect_for_models
from angee.graphql.data.hasura import AngeeHasuraWriteBackend
from angee.graphql.publishing import publish_bulk_update
from angee.provenance.context import (
    changeset,
    changeset_model,
    changeset_model_label,
    require_changeset,
)


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


class _ChangesetExtension(SchemaExtension):
    """Mark native handled errors on the operation's attribution boundary."""

    def _mark_failed(self, mark_failed: Callable[[], None]) -> None:
        result = self.execution_context.result
        if self.execution_context.pre_execution_errors or result is not None and result.errors:
            mark_failed()


class ChangesetExtension(_ChangesetExtension):
    """Join transport attribution, or own a boundary for direct sync execution."""

    def on_operation(self) -> Iterator[None]:
        with changeset() as mark_failed:
            yield
            self._mark_failed(mark_failed)


class AsyncChangesetExtension(_ChangesetExtension):
    """Close direct async execution in the attribution owner's worker thread."""

    async def on_operation(self) -> AsyncIterator[None]:
        async with changeset() as mark_failed:
            yield
            self._mark_failed(mark_failed)


def schema_extension(*, sync: bool) -> type[SchemaExtension]:
    """Select the native operation hook for Strawberry's execution mode."""

    return ChangesetExtension if sync else AsyncChangesetExtension


def setup_graphql() -> None:
    """Bind changeset closure publication without changing storage behavior."""

    connect_for_models(
        bulk_updated, partial(publish_bulk_update, broadcast=False),
        applies=lambda model: model._meta.label_lower == changeset_model_label().lower(),
        dispatch_uid="provenance.changeset_closed",
    )
