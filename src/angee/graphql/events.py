"""Payload and GraphQL event types for model change subscriptions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast
from uuid import uuid4

import strawberry
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from strawberry.scalars import JSON

from angee.base.hooks import resolve_hooks
from angee.base.models.identity import public_id_of
from angee.base.refs import RecordRef, record_view_refs
from angee.base.serialization import json_safe

ReadableFields = Iterable[str] | Callable[[], Iterable[str]]
"""Readable model fields, resolved lazily only when building a partial-update payload."""


@dataclass(frozen=True, slots=True)
class ChangeRelatedRecord:
    """Canonical parent record whose authored reads depend on this changed row."""

    model: str
    id: str

    @classmethod
    def for_records(cls, *records: RecordRef) -> tuple[ChangeRelatedRecord, ...]:
        """Include existing inherited views of a canonical concern identity."""
        return tuple(cls(record.model_label, record.public_id) for record in record_view_refs(*records))


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

    instance: models.Model | None = field(default=None, compare=False, repr=False)
    """Source row available during transport projection, including deletes; never serialized."""

    using: str | None = None
    """Database alias captured before local observation; never serialized."""

    broadcasts: bool = True
    """Whether this occurrence reaches the channel layer; local observers still receive it."""

    @classmethod
    def from_instance(
        cls,
        instance: models.Model,
        *,
        action: str,
        update_fields: Iterable[str] | None,
        readable_fields: ReadableFields = (),
        during_ingestion: bool = False,
        broadcasts: bool = True,
        field_values: Mapping[str, Any] | None = None,
    ) -> ChangePayload:
        """Return the channel payload for a saved or deleted model instance."""

        changed_fields = tuple(sorted(str(field) for field in update_fields)) if update_fields is not None else None
        changed_values = None
        if changed_fields is not None:
            readable = readable_fields() if callable(readable_fields) else readable_fields
            changed_values = _changed_values(instance, changed_fields, frozenset(readable), field_values)

        related_records = _change_related_records(instance) if broadcasts else ()
        change = cls(
            model=instance._meta.label,
            id=public_id_of(instance),
            action=action,
            occurrence_id=uuid4().hex,
            changed_fields=changed_fields,
            changed_values=changed_values,
            related_records=related_records,
            during_ingestion=during_ingestion,
            instance=instance,
            broadcasts=broadcasts,
            using=instance._state.db,
        )

        for provider in resolve_hooks("ANGEE_GRAPHQL_CHANGE_PROJECTORS"):
            change = provider(instance, change)
        return change

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
            payload["related_records"] = [{"model": item.model, "id": item.id} for item in self.related_records]
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
    readable_fields: frozenset[str],
    field_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return projected concrete-field values, including inherited columns, without relation fetches."""

    values: dict[str, Any] = {}
    for name in changed_fields:
        field = _concrete_field(instance, name)
        if field is None or not ({name, field.name, field.attname} & readable_fields):
            continue
        value = field_values.get(name) if field_values is not None else getattr(instance, field.attname, None)
        values[name] = json_safe(value)
    return values


def _change_related_records(instance: models.Model) -> tuple[ChangeRelatedRecord, ...]:
    """Project model-owned native references without widening their declared views.

    Live historical hooks in ARP and framework callers still return the wire
    value; accept those during their companion migration. New hooks return
    ``RecordRef`` and own any explicit ``record_view_refs`` expansion themselves.
    """

    resolver = getattr(instance, "change_related_records", None)
    if not callable(resolver):
        return ()
    records = tuple(resolver())
    if any(not isinstance(record, RecordRef | ChangeRelatedRecord) for record in records):
        raise TypeError("change_related_records() must return RecordRef values")
    return tuple(
        ChangeRelatedRecord(record.model_label, record.public_id) if isinstance(record, RecordRef) else record
        for record in records
    )


def _concrete_field(
    instance: models.Model,
    name: str,
) -> models.Field[Any, Any] | None:
    """Return a concrete field on this row addressed by ``name`` or its attname."""

    field: models.Field[Any, Any] | models.ForeignObjectRel | None
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
    """Read-gated model change, or an empty-id ``ready`` registration control.

    Readiness contains no record data or occurrence identity and follows replayed
    data. Its opaque cursor marks the completed registration replay.
    """

    model: str
    id: strawberry.ID
    action: str
    replay_cursor: str | None = None
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
                ChangeRelatedRecordType(model=item.model, id=strawberry.ID(item.id)) for item in payload.related_records
            ],
        )
