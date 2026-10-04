"""Payload and GraphQL event types for model change subscriptions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import uuid4

import strawberry
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from rebac import ObjectRef
from rebac.resources import model_resource_id, model_resource_type
from strawberry.scalars import JSON

from angee.base.identity import public_id_for, public_id_of
from angee.base.refs import RecordRef, concrete_child_models
from angee.base.serialization import json_safe

ReadableFields = Iterable[str] | Callable[[], Iterable[str]]
"""Readable model fields, resolved lazily only when building a partial-update payload."""


@dataclass(frozen=True, slots=True)
class ChangeRelatedRecord:
    """Canonical parent record whose authored reads depend on this changed row."""

    model: str
    id: str

    @classmethod
    def for_record(cls, record: RecordRef) -> tuple[ChangeRelatedRecord, ...]:
        """Include existing inherited views of a canonical concern identity."""
        model = apps.get_model(record.model_label)
        result = [cls(record.model_label, record.public_id)]

        def children(parent: type[models.Model]) -> None:
            for child in concrete_child_models(parent):
                if child._base_manager.filter(pk=record.object_id).exists():
                    result.append(cls(child._meta.label, public_id_for(child, record.object_id)))
                    children(child)

        children(model)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ChangePayload:
    """Channel-layer payload describing one model row change."""

    model: str
    """Django model label for the changed row."""

    id: str
    """Public row identifier exposed to GraphQL clients."""

    action: str
    """Change action: create, update, or delete."""

    occurrence_id: str | None = None
    """Publisher-created identity for this observable change occurrence."""

    changed_fields: tuple[str, ...] | None = None
    """Updated model fields when Django saved a partial update."""

    changed_values: Mapping[str, Any] | None = None
    """JSON-safe changed values keyed by field name."""

    resource_id: str | None = None
    """REBAC resource id when it differs from the public id."""

    read_resource_type: str | None = None
    """Optional resource type whose ``read`` permission gates this event."""

    read_resource_id: str | None = None
    """Optional resource id paired with :attr:`read_resource_type`."""

    related_records: tuple[ChangeRelatedRecord, ...] = ()
    """Readable parent records whose exact authored reads depend on this change."""

    during_ingestion: bool = False
    """Whether the change happened inside a sync ingestion context; not part of the wire message."""

    @classmethod
    def from_instance(
        cls,
        instance: models.Model,
        *,
        action: str,
        update_fields: Iterable[str] | None,
        readable_fields: ReadableFields = (),
        during_ingestion: bool = False,
    ) -> ChangePayload:
        """Return the channel payload for a saved or deleted model instance."""

        changed_fields = tuple(sorted(str(field) for field in update_fields)) if update_fields is not None else None
        changed_values = None
        if changed_fields is not None:
            readable = readable_fields() if callable(readable_fields) else readable_fields
            changed_values = _changed_values(instance, changed_fields, frozenset(readable))
        resource_id = None
        if model_resource_type(type(instance)):
            resource_id = model_resource_id(instance)
        read_resource = _change_read_resource(instance)
        related_records = _change_related_records(instance)
        return cls(
            model=instance._meta.label,
            id=public_id_of(instance),
            action=action,
            occurrence_id=uuid4().hex,
            changed_fields=changed_fields,
            changed_values=changed_values,
            resource_id=resource_id,
            read_resource_type=None if read_resource is None else read_resource.resource_type,
            read_resource_id=None if read_resource is None else read_resource.resource_id,
            related_records=related_records,
            during_ingestion=during_ingestion,
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
            occurrence_id=(str(payload["occurrence_id"]) if payload.get("occurrence_id") is not None else None),
            changed_fields=changed_fields,
            changed_values=dict(values) if isinstance(values, Mapping) else None,
            resource_id=str(payload["resource_id"]) if payload.get("resource_id") is not None else None,
            read_resource_type=(
                str(payload["read_resource_type"]) if payload.get("read_resource_type") is not None else None
            ),
            read_resource_id=(
                str(payload["read_resource_id"]) if payload.get("read_resource_id") is not None else None
            ),
            related_records=tuple(
                ChangeRelatedRecord(model=str(item["model"]), id=str(item["id"]))
                for item in payload.get("related_records", ())
                if isinstance(item, Mapping) and item.get("model") and item.get("id")
            ),
        )

    def as_message(self) -> dict[str, Any]:
        """Return the channel-layer dictionary representation."""

        payload: dict[str, Any] = {
            "model": self.model,
            "id": self.id,
            "action": self.action,
            "changed_fields": list(self.changed_fields) if self.changed_fields is not None else None,
            "changed_values": dict(self.changed_values) if self.changed_values is not None else None,
        }
        if self.occurrence_id is not None:
            payload["occurrence_id"] = self.occurrence_id
        if self.resource_id is not None:
            payload["resource_id"] = self.resource_id
        if self.read_resource_type is not None and self.read_resource_id is not None:
            payload["read_resource_type"] = self.read_resource_type
            payload["read_resource_id"] = self.read_resource_id
        if self.related_records:
            payload["related_records"] = [
                {"model": item.model, "id": item.id} for item in self.related_records
            ]
        return payload

    @property
    def resource_identifier(self) -> str:
        """Return the REBAC resource id, falling back to the public id."""

        return self.resource_id or self.id

    @property
    def read_resource(self) -> ObjectRef | None:
        """Return the model-selected event read anchor, if one was captured."""

        if self.read_resource_type is None or self.read_resource_id is None:
            return None
        return ObjectRef(self.read_resource_type, self.read_resource_id)

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
    readable_fields: frozenset[str],
) -> dict[str, Any]:
    """Return projected concrete-field values, including inherited columns, without relation fetches."""

    values: dict[str, Any] = {}
    for name in changed_fields:
        field = _concrete_field(instance, name)
        if field is None or not ({name, field.name, field.attname} & readable_fields):
            continue
        values[name] = json_safe(getattr(instance, field.attname, None))
    return values


def _change_read_resource(instance: models.Model) -> ObjectRef | None:
    """Return an instance-owned change-feed read anchor, if declared.

    Most rows are gated by their own REBAC identity. A child or polymorphic edge
    whose visibility derives from another row may expose ``change_read_resource``;
    capturing that :class:`~rebac.ObjectRef` while the instance is still live keeps
    create, update, and delete events on the same authorization boundary.
    """

    resolver = getattr(instance, "change_read_resource", None)
    if not callable(resolver):
        return None
    resource = resolver()
    if resource is not None and not isinstance(resource, ObjectRef):
        raise TypeError("change_read_resource() must return rebac.ObjectRef or None")
    return resource


def _change_related_records(instance: models.Model) -> tuple[ChangeRelatedRecord, ...]:
    """Return model-owned canonical parents for exact authored-query invalidation."""

    resolver = getattr(instance, "change_related_records", None)
    if not callable(resolver):
        return ()
    records = tuple(resolver())
    if any(not isinstance(record, ChangeRelatedRecord) for record in records):
        raise TypeError("change_related_records() must return ChangeRelatedRecord values")
    return records


def _concrete_field(
    instance: models.Model,
    name: str,
) -> models.Field[Any, Any] | None:
    """Return a concrete field on this row addressed by ``name`` or its attname."""

    try:
        field = instance._meta.get_field(name)
    except FieldDoesNotExist:
        field = next((item for item in instance._meta.concrete_fields if item.attname == name), None)
    if not isinstance(field, models.Field) or not field.concrete:
        return None
    if field not in instance._meta.concrete_fields:
        return None
    return field


@strawberry.type
class ChangeRelatedRecordType:
    """Canonical related record exposed with an authorized change event."""

    model: str
    id: strawberry.ID


@strawberry.type
class ChangeEvent:
    """Read-gated notification that one model instance changed."""

    model: str
    id: strawberry.ID
    action: str
    occurrence_id: str | None = None
    changed_fields: list[str] | None = None
    changed_values: JSON | None = None
    related_records: list[ChangeRelatedRecordType] = strawberry.field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: ChangePayload | Mapping[str, Any]) -> ChangeEvent:
        """Return a GraphQL event from a change payload."""

        payload = payload if isinstance(payload, ChangePayload) else ChangePayload.from_mapping(payload)
        return cls(
            model=payload.model,
            id=strawberry.ID(payload.id),
            action=payload.action,
            occurrence_id=payload.occurrence_id,
            changed_fields=list(payload.changed_fields) if payload.changed_fields is not None else None,
            changed_values=cast(JSON | None, payload.changed_values),
            related_records=[
                ChangeRelatedRecordType(model=item.model, id=strawberry.ID(item.id))
                for item in payload.related_records
            ],
        )
