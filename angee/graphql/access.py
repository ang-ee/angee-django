"""REBAC read gating for model change payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import models
from rebac import ObjectRef, SubjectRef
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields
from rebac.resources import model_resource_type

from angee.graphql.events import ChangeEvent, ChangePayload


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

        Each gated field performs one backend check.
        """

        if not self.gated_fields or payload.changed_fields is None:
            return payload

        denied = {
            field
            for field in payload.changed_fields
            if field in self.gated_fields
            and not self._can_read_field(
                field,
                resource,
            )
        }
        return payload.redacted(denied)

    def _can_read_field(self, field: str, resource: ObjectRef) -> bool:
        """Return whether the actor may read one gated field."""

        result = check_field_access(
            self.active_backend,
            subject=self.actor,
            action=f"read__{field}",
            resource=resource,
        )
        return bool(result.allowed)
