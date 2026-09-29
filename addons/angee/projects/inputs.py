"""Data-only inputs for the project setup owner and its contributors."""

from __future__ import annotations

from datetime import date
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as InputValidationError
from rebac import current_actor


class MilestoneTemplate(BaseModel):
    """A milestone's name is its stable identity within a setup template."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    start_date: date | None = None
    target_date: date | None = None

    @classmethod
    def values(cls, value: Any) -> dict[str, Any]:
        """Translate template errors into the standard model validation contract."""

        try:
            return cls.model_validate(value).model_dump()
        except InputValidationError as error:
            raise ValidationError({"milestones": [issue["msg"] for issue in error.errors()]}) from error


def setup_reference(model_label: str, value: str, *, permission: str = "read") -> Any:
    """Resolve a declared public reference under the requesting actor's permission."""

    model = apps.get_model(model_label)
    actor = current_actor()
    row = None if actor is None else model.objects.with_actor(actor).with_action(permission).from_public_id(str(value))
    if row is None:
        raise ValidationError({"configuration": f"An accessible {model_label} is required."})
    return row
