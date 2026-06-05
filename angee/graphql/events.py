"""GraphQL event type for model change subscriptions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class ChangeEvent:
    """Read-gated notification that one model instance changed."""

    model: str
    id: strawberry.ID
    action: str
    changed_fields: list[str] | None = None
    changed_values: JSON | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ChangeEvent:
        """Return a GraphQL event from a change payload mapping."""

        fields = payload.get("changed_fields")
        return cls(
            model=str(payload["model"]),
            id=strawberry.ID(str(payload["id"])),
            action=str(payload["action"]),
            changed_fields=cast(list[str] | None, fields),
            changed_values=cast(JSON | None, payload.get("changed_values")),
        )
