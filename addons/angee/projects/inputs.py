"""Data-only inputs for the project setup owner and its contributors."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from angee.base.validation import validate_value


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

        return validate_value(cls, value, field="milestones").model_dump()
