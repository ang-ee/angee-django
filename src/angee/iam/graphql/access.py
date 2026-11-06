"""Native role admission and REBAC read gates for GraphQL surfaces."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any, ClassVar

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac import ObjectRef, SubjectRef, current_actor
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields, visible_id_sets
from rebac.resources import model_resource_type
from rebac.schema import Definition
from rebac.schema.walker import field_gated_actions
from rebac.types import CheckItem
from strawberry.permission import BasePermission
from strawberry_django.auth.utils import get_current_user

from angee.base.models.identity import instances_from_public_ids
from angee.graphql.events import ChangeEvent, ChangePayload, ChangeRelatedRecord
from angee.iam.permissions import (
    current_actor_has_role,
    effective_rebac_definition,
    is_platform_admin,
    require_rebac_queryset,
)


class RolePermission(BasePermission):
    """Allow actors that reach ``role_ref`` through ``effective_member``."""

    role_ref: ClassVar[ObjectRef | None] = None

    message = "Role permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the current actor reaches the configured role."""

        del source, kwargs
        return self.role_ref is not None and current_actor_has_role(self.role_ref)


class PlatformAdminPermission(RolePermission):
    """Allow only actors that reach IAM's const-backed platform admin role."""

    message = "Platform admin permission required."

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the request user has platform-admin reach."""

        del source, kwargs
        return is_platform_admin(get_current_user(info))


ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]


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
    model: type[models.Model],
    field_names: Iterable[str],
    owner: str,
    reason: str,
    *,
    definition_for: Callable[[type[models.Model]], Definition | None] = effective_rebac_definition,
) -> None:
    if gated := sorted(
        name for name in set(field_names) if is_gated_read_axis(model, name, definition_for=definition_for)
    ):
        raise ImproperlyConfigured(f"{model._meta.label}: {owner} {gated} are field-gated reads; {reason}")


def is_gated_read_axis(
    model: type[models.Model],
    axis: str,
    *,
    definition_for: Callable[[type[models.Model]], Definition | None] = effective_rebac_definition,
) -> bool:
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
        if field_name in _gated_read_fields(leaf_model, definition_for=definition_for):
            return True
        try:
            field = leaf_model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return False
        related = getattr(field, "related_model", None)
        if related is None:
            return False
        leaf_model = related
    return leaf.split(".", maxsplit=1)[0] in _gated_read_fields(leaf_model, definition_for=definition_for)


def _gated_read_fields(
    model: type[models.Model],
    *,
    definition_for: Callable[[type[models.Model]], Definition | None],
) -> frozenset[str]:
    """Resolve native gate declarations through the caller's policy authority."""

    definition = definition_for(model)
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

        resource = (
            ObjectRef(change.read_resource_type, change.read_resource_id)
            if change.read_resource_type is not None and change.read_resource_id is not None
            else ObjectRef(self.resource_type, change.resource_identifier)
        )
        allowed = check_field_access(
            self.active_backend,
            subject=self.actor,
            action="read",
            resource=resource,
        )
        if not allowed.allowed:
            return None
        return self._emit(change)

    def filter_many(self, payloads: tuple[ChangePayload, ...]) -> tuple[ChangeEvent, ...]:
        """Gate a bounded replay batch with one native bulk permission check."""

        if type(self) is not ChangeReadGate:
            return tuple(event for payload in payloads if (event := self.filter(payload)) is not None)
        if not self.resource_type:
            return tuple(self._emit(payload) for payload in payloads)
        items = [CheckItem(
            self.actor, "read",
            ObjectRef(
                payload.read_resource_type if payload.read_resource_type is not None
                and payload.read_resource_id is not None else self.resource_type,
                payload.read_resource_id if payload.read_resource_type is not None
                and payload.read_resource_id is not None else payload.resource_identifier,
            ),
        ) for payload in payloads]
        answers = self.active_backend.check_bulk_permissions(items)
        return tuple(self._emit(payload) for payload, answer in zip(payloads, answers, strict=True) if answer.allowed)

    def _change(
        self,
        payload: Mapping[str, Any] | ChangePayload,
    ) -> ChangePayload:
        """Normalize one channel value into the gate's policy input."""

        return payload if isinstance(payload, ChangePayload) else ChangePayload.from_mapping(payload)

    def _emit(self, change: ChangePayload) -> ChangeEvent:
        """Redact one policy-approved change and project its GraphQL event."""

        change = self._filter_related_records(change)
        if not self.resource_type:
            return ChangeEvent.from_payload(change)
        resource = ObjectRef(self.resource_type, change.resource_identifier)
        return ChangeEvent.from_payload(self._redact(change, resource))

    def _filter_related_records(self, change: ChangePayload) -> ChangePayload:
        """Retain only canonical related rows readable by this subscriber."""

        references: list[tuple[ChangeRelatedRecord, type[models.Model]]] = []
        by_model: dict[type[models.Model], list[str]] = {}
        for reference in change.related_records:
            try:
                model = apps.get_model(reference.model)
            except LookupError, ValueError:
                continue
            references.append((reference, model))
            by_model.setdefault(model, []).append(reference.id)

        readable_ids: dict[type[models.Model], set[str]] = {}
        for model, ids in by_model.items():
            scope = require_rebac_queryset(model).with_actor(self.actor).with_action("read")
            readable_ids[model] = set(instances_from_public_ids(model, ids, queryset=scope))
        readable = [reference for reference, model in references if reference.id in readable_ids[model]]
        return replace(change, related_records=tuple(readable))

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

        fields = frozenset(payload.changed_fields) & self.gated_fields
        visible = visible_id_sets(
            model=self.model, actor=self.actor, fields=fields,
            resource_ids=(resource.resource_id,), active_backend=self.active_backend,
        )
        denied = {field_name for field_name in fields if resource.resource_id not in visible[field_name]}
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
