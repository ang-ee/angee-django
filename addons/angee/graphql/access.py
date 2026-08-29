"""REBAC read gating for GraphQL schema surfaces and change payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from angee.base.permissions import effective_rebac_definition
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac import ObjectRef, SubjectRef, current_actor
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields
from rebac.resources import model_resource_type
from rebac.schema.walker import field_gated_actions

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


def assert_no_gated_read_fields(model: type[models.Model], field_names: Iterable[str], owner: str, reason: str) -> None:
    if gated := sorted(name for name in set(field_names) if is_gated_read_axis(model, name)):
        raise ImproperlyConfigured(f"{model._meta.label}: {owner} {gated} are field-gated reads; {reason}")


def is_gated_read_axis(model: type[models.Model], axis: str) -> bool:
    """Whether a (possibly relation-leaf) group-by axis reads a field-gated column.

    A dotted axis (``party__display_name``) is never a field on ``model``, so it
    would slip past a same-model check; walk its forward to-one relations to the
    leaf model and gate-check the leaf there — a gated read reached through a
    relation leaks owner-only values into bucket keys exactly as a direct one does.
    """

    *path, leaf = axis.split("__")
    leaf_model: type[models.Model] = model
    for step in path:
        field_name = step.split(".", maxsplit=1)[0]
        if field_name in _declared_gated_read_fields(leaf_model):
            return True
        try:
            field = leaf_model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return False
        related = getattr(field, "related_model", None)
        if related is None:
            return False
        leaf_model = related
    return leaf.split(".", maxsplit=1)[0] in _declared_gated_read_fields(leaf_model)


def _declared_gated_read_fields(model: type[models.Model]) -> frozenset[str]:
    """Return field names protected by disk-declared ``read__`` permissions."""

    definition = effective_rebac_definition(model)
    if definition is None:
        # Untyped models cannot cause the backend helper to resolve a schema, and
        # retaining that empty fast path keeps synthetic callers injectable.
        return gated_read_fields(model) if not model_resource_type(model) else frozenset()
    prefix = "read__"
    fields = {
        field_name
        for action in field_gated_actions(definition, "read")
        if (field_name := _model_field_name(model, action.removeprefix(prefix))) is not None
    }
    return frozenset(fields)


def _model_field_name(model: type[models.Model], name: str) -> str | None:
    """Resolve a declared field action to its canonical Django field name."""

    if name == "pk":
        pk = model._meta.pk
        return pk.name if pk is not None else None
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist:
        return next(
            (
                candidate.name
                for candidate in model._meta.concrete_fields
                if getattr(candidate, "attname", None) == name
            ),
            None,
        )
    return field.name if getattr(field, "concrete", False) else None


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

        change = self._change(payload)
        if not self.resource_type:
            return self._emit(change)

        resource = change.read_resource or ObjectRef(self.resource_type, change.resource_identifier)
        allowed = check_field_access(
            self.active_backend,
            subject=self.actor,
            action="read",
            resource=resource,
        )
        if not allowed.allowed:
            return None
        return self._emit(change)

    def _change(
        self,
        payload: Mapping[str, Any] | ChangePayload,
    ) -> ChangePayload:
        """Normalize one channel value into the gate's policy input."""

        return payload if isinstance(payload, ChangePayload) else ChangePayload.from_mapping(payload)

    def _emit(self, change: ChangePayload) -> ChangeEvent:
        """Redact one policy-approved change and project its GraphQL event."""

        if not self.resource_type:
            return ChangeEvent.from_payload(change)
        resource = ObjectRef(self.resource_type, change.resource_identifier)
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


class ActorSelfChangeReadGate(ChangeReadGate):
    """Expose changes only when the changed resource is the subscribing actor.

    Self-service schema surfaces use this gate when ordinary row-read permission
    is intentionally broader than the private event stream. The actor/resource
    identity is still derived by REBAC; no session or model-specific identifier
    is reimplemented here.
    """

    def filter(
        self,
        payload: Mapping[str, Any] | ChangePayload,
    ) -> ChangeEvent | None:
        """Return only this actor's own resource change event."""

        change = self._change(payload)
        if self.actor.optional_relation:
            return None
        if self.resource_type != self.actor.subject_type:
            return None
        if change.resource_identifier != self.actor.subject_id:
            return None
        return self._emit(change)
