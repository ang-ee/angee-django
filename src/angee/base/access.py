"""REBAC read gating for model change payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import models
from rebac import ObjectRef, SubjectRef
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields
from rebac.resources import model_resource_type

from angee.base.graphql.events import ChangeEvent


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
        payload: Mapping[str, Any],
    ) -> ChangeEvent | None:
        """Return a readable change event, or ``None`` when hidden."""

        if not self.resource_type:
            return ChangeEvent.from_payload(payload)

        resource = ObjectRef(self.resource_type, str(payload["id"]))
        allowed = check_field_access(
            self.active_backend,
            subject=self.actor,
            action="read",
            resource=resource,
        )
        if not allowed.allowed:
            return None
        return ChangeEvent.from_payload(self._redact(payload, resource))

    def _redact(
        self,
        payload: Mapping[str, Any],
        resource: ObjectRef,
    ) -> dict[str, Any]:
        """Return ``payload`` with unreadable field-gated values removed.

        Each gated field performs one backend check.
        """

        fields = payload.get("changed_fields")
        values = payload.get("changed_values")
        if not self.gated_fields or not isinstance(fields, list):
            return dict(payload)

        denied = {
            field
            for field in fields
            if field in self.gated_fields
            and not self._can_read_field(
                field,
                resource,
            )
        }
        if not denied:
            return dict(payload)

        redacted = dict(payload)
        redacted["changed_fields"] = [
            field for field in fields if field not in denied
        ]
        if isinstance(values, Mapping):
            redacted["changed_values"] = {
                field: value
                for field, value in values.items()
                if field not in denied
            }
        return redacted

    def _can_read_field(self, field: str, resource: ObjectRef) -> bool:
        """Return whether the actor may read one gated field."""

        result = check_field_access(
            self.active_backend,
            subject=self.actor,
            action=f"read__{field}",
            resource=resource,
        )
        return bool(result.allowed)
