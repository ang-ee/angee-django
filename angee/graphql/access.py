"""REBAC read gating for GraphQL schema surfaces and change payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac import ObjectRef, SubjectRef, current_actor
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields
from rebac.resources import model_resource_type

from angee.graphql.events import ChangeEvent, ChangePayload


def actor_can_read(resource: ObjectRef) -> bool:
    """Return whether the current actor holds ``read`` on ``resource``.

    The GraphQL-layer read gate for surfaces that anchor visibility on a single
    REBAC object rather than a per-model resource (e.g. the platform console's
    ``platform/explorer`` anchor, the operator daemon's ``operator/connection``
    anchor). Callers pass their own anchor as ``resource`` so each surface keeps
    its anchor explicit; an actorless request (no authenticated subject) reads as
    not allowed.
    """

    actor = current_actor()
    if actor is None:
        return False
    return check_field_access(backend(), subject=actor, action="read", resource=resource).allowed


def assert_no_gated_read_fields(
    model: type[models.Model], field_names: Iterable[str], owner: str, reason: str
) -> None:
    if gated := sorted(name for name in set(field_names) if _is_gated_read_axis(model, name)):
        raise ImproperlyConfigured(f"{model._meta.label}: {owner} {gated} are field-gated reads; {reason}")


def _is_gated_read_axis(model: type[models.Model], axis: str) -> bool:
    """Whether a (possibly relation-leaf) group-by axis reads a field-gated column.

    A dotted axis (``party__display_name``) is never a field on ``model``, so it
    would slip past a same-model check; walk its forward to-one relations to the
    leaf model and gate-check the leaf there — a gated read reached through a
    relation leaks owner-only values into bucket keys exactly as a direct one does.
    """

    *path, leaf = axis.split("__")
    leaf_model: type[models.Model] = model
    for step in path:
        related = getattr(leaf_model._meta.get_field(step), "related_model", None)
        if related is None:
            return False
        leaf_model = related
    return leaf in gated_read_fields(leaf_model)


class ChangeReadGate:
    """Filter and redact change payloads for one model and actor."""

    def __init__(
        self,
        model: type[models.Model],
        actor: SubjectRef,
    ) -> None:
        """Resolve model authorization facts for ``actor`` once."""

        self.model = model
        self.actor = actor
        self.resource_type = model_resource_type(model)
        self.gated_fields = gated_read_fields(model)
        self.active_backend = backend()

    def filter(
        self,
        payload: Mapping[str, Any] | ChangePayload,
    ) -> ChangeEvent | None:
        """Return a readable change event, or ``None`` when hidden."""

        change = payload if isinstance(payload, ChangePayload) else ChangePayload.from_mapping(payload)
        if not self.resource_type:
            return ChangeEvent.from_payload(change)

        resource = ObjectRef(self.resource_type, change.resource_identifier)
        allowed = check_field_access(
            self.active_backend,
            subject=self.actor,
            action="read",
            resource=resource,
        )
        if not allowed.allowed:
            return None
        return ChangeEvent.from_payload(self._redact(change, resource))

    def _redact(
        self,
        payload: ChangePayload,
        resource: ObjectRef,
    ) -> ChangePayload:
        """Return ``payload`` with unreadable field-gated values removed.

        The resource-level ``read`` check already decided whether the actor may
        receive this delivery. Changed field-gated values then ask the same
        upstream ``read__<field>`` owner used by ordinary query redaction, so a
        row-readable actor keeps fields it may read and loses only denied ones.
        """

        if not self.gated_fields or payload.changed_fields is None:
            return payload

        denied: set[str] = set()
        for field_name in set(payload.changed_fields) & set(self.gated_fields):
            result = check_field_access(
                self.active_backend,
                subject=self.actor,
                action=f"read__{field_name}",
                resource=resource,
            )
            if not result.allowed:
                denied.add(field_name)
        return payload.redacted(denied)
