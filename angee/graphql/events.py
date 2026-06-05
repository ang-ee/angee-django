"""Payload and GraphQL event types for model change subscriptions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

import strawberry
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from rebac.resources import model_resource_id, model_resource_type
from strawberry.scalars import JSON

from angee.base.models import public_id_of
from angee.base.serialization import json_safe


@dataclass(frozen=True, slots=True)
class ChangePayload:
    """Channel-layer payload describing one model row change."""

    model: str
    """Django model label for the changed row."""

    id: str
    """Public row identifier exposed to GraphQL clients."""

    action: str
    """Change action: create, update, or delete."""

    changed_fields: tuple[str, ...] | None = None
    """Updated model fields when Django saved a partial update."""

    changed_values: Mapping[str, Any] | None = None
    """JSON-safe changed values keyed by field name."""

    resource_id: str | None = None
    """REBAC resource id when it differs from the public id."""

    @classmethod
    def from_instance(
        cls,
        instance: models.Model,
        *,
        action: str,
        update_fields: Iterable[str] | None,
    ) -> ChangePayload:
        """Return the channel payload for a saved or deleted model instance."""

        changed_fields = tuple(sorted(str(field) for field in update_fields)) if update_fields is not None else None
        changed_values = _changed_values(instance, changed_fields) if changed_fields is not None else None
        resource_id = None
        if model_resource_type(type(instance)):
            resource_id = model_resource_id(instance)
        return cls(
            model=instance._meta.label,
            id=public_id_of(instance),
            action=action,
            changed_fields=changed_fields,
            changed_values=changed_values,
            resource_id=resource_id,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ChangePayload:
        """Return a payload from its channel-layer message dictionary."""

        fields = payload.get("changed_fields")
        changed_fields = tuple(str(field) for field in fields) if isinstance(fields, list | tuple) else None
        values = payload.get("changed_values")
        return cls(
            model=str(payload["model"]),
            id=str(payload["id"]),
            action=str(payload["action"]),
            changed_fields=changed_fields,
            changed_values=dict(values) if isinstance(values, Mapping) else None,
            resource_id=str(payload["resource_id"]) if payload.get("resource_id") is not None else None,
        )

    def as_message(self) -> dict[str, Any]:
        """Return the channel-layer dictionary representation."""

        payload = {
            "model": self.model,
            "id": self.id,
            "action": self.action,
            "changed_fields": list(self.changed_fields) if self.changed_fields is not None else None,
            "changed_values": dict(self.changed_values) if self.changed_values is not None else None,
        }
        if self.resource_id is not None:
            payload["resource_id"] = self.resource_id
        return payload

    @property
    def resource_identifier(self) -> str:
        """Return the REBAC resource id, falling back to the public id."""

        return self.resource_id or self.id

    def redacted(self, denied_fields: set[str]) -> ChangePayload:
        """Return a payload with denied field-level values removed."""

        if not denied_fields or self.changed_fields is None:
            return self
        changed_fields = tuple(field for field in self.changed_fields if field not in denied_fields)
        changed_values = (
            {field: value for field, value in self.changed_values.items() if field not in denied_fields}
            if self.changed_values is not None
            else None
        )
        return replace(self, changed_fields=changed_fields, changed_values=changed_values)


def _changed_values(
    instance: models.Model,
    changed_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Return JSON-safe values for concrete local fields without relation fetches."""

    values: dict[str, Any] = {}
    for name in changed_fields:
        field = _concrete_local_field(instance, name)
        if field is not None:
            values[name] = json_safe(getattr(instance, field.attname, None))
    return values


def _concrete_local_field(
    instance: models.Model,
    name: str,
) -> models.Field[Any, Any] | None:
    """Return the concrete local field addressed by ``name`` or its attname."""

    try:
        field = instance._meta.get_field(name)
    except FieldDoesNotExist:
        field = next((item for item in instance._meta.local_fields if item.attname == name), None)
    if not isinstance(field, models.Field) or not field.concrete:
        return None
    if field not in instance._meta.local_fields:
        return None
    return field


@strawberry.type
class ChangeEvent:
    """Read-gated notification that one model instance changed."""

    model: str
    id: strawberry.ID
    action: str
    changed_fields: list[str] | None = None
    changed_values: JSON | None = None

    @classmethod
    def from_payload(cls, payload: ChangePayload | Mapping[str, Any]) -> ChangeEvent:
        """Return a GraphQL event from a change payload."""

        payload = payload if isinstance(payload, ChangePayload) else ChangePayload.from_mapping(payload)
        return cls(
            model=payload.model,
            id=strawberry.ID(payload.id),
            action=payload.action,
            changed_fields=list(payload.changed_fields) if payload.changed_fields is not None else None,
            changed_values=cast(JSON | None, payload.changed_values),
        )
