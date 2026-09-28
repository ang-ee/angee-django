"""Validated, immutable round provisioning input."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from angee.base.scoping import system_queryset


class TopicTemplate(BaseModel):
    """One ordered topic in a round template."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    hint: str = ""

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        """Use the topic owner's case-insensitive identity."""

        value = value.strip().lower()
        if not value:
            raise ValueError("Topic key is required.")
        return value


class RoundTemplate(BaseModel):
    """Portable settings and topics, with milestone names resolved on the target."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    opening_policy: str = "facilitator_only"
    roster_visibility: str = "hidden"
    clarification_askers: str = "hidden"
    last_call_at: datetime
    submission_deadline: datetime
    topics: tuple[TopicTemplate, ...] = ()
    tracks: bool = False
    opens_after: str | None = None
    clarifications_shared_until: str | None = None

    @model_validator(mode="after")
    def unique_topics(self) -> RoundTemplate:
        """Reject ambiguous ordered topic declarations."""

        if len({topic.key for topic in self.topics}) != len(self.topics):
            raise ValueError("Topic keys must be unique.")
        return self

    def round_values(self, project: Any) -> dict[str, Any]:
        """Resolve milestone names once and validate choices through model fields."""

        values = self.model_dump(exclude={"topics", "tracks", "opens_after", "clarifications_shared_until"})
        round_model = apps.get_model("proposals", "Round")
        candidate = round_model(**values)
        for name, value in values.items():
            round_model._meta.get_field(name).clean(value, candidate)
        for name in ("opens_after", "clarifications_shared_until"):
            milestone_name = getattr(self, name)
            milestone = None
            if milestone_name is not None:
                if project is None:
                    raise ValidationError({name: "A milestone requires a target project."})
                matches = list(
                    system_queryset(apps.get_model("projects", "Milestone")).filter(
                        project=project,
                        name=milestone_name,
                    )[:2]
                )
                if len(matches) != 1:
                    raise ValidationError({name: "The milestone name must identify one target-project milestone."})
                milestone = matches[0]
            values[f"{name}_id"] = milestone.pk if milestone else None
        return values
