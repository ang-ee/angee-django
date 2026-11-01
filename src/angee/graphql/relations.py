"""Transport-neutral record pointer projection."""

from __future__ import annotations
from typing import Self
import strawberry
from angee.base.refs import RecordRef


@strawberry.type
class RecordPointerType:
    """Minimal record identity from an already authorized reference."""

    label: str
    model_label: str = strawberry.field(name="model_label")
    record_id: strawberry.ID = strawberry.field(name="record_id")

    @classmethod
    def from_reference(cls, reference: RecordRef, *, label: str = "") -> Self:
        """Project identity without loading the target; navigation rechecks access."""

        return cls(
            label=label or reference.public_id,
            model_label=reference.model_label,
            record_id=strawberry.ID(reference.public_id),
        )
