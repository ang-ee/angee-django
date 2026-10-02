"""Typed requests and the single retained evidence context."""

from datetime import datetime
from typing import Any

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, InstanceOf, field_validator, model_validator

from angee.base.evidence import EvidenceFact, EvidenceReference
from angee.decisions.forms import Action


class DecisionRecordReference(EvidenceReference):
    """One public record identity and optional navigation hints in retained context."""

    label: str = ""
    tab: str | None = None
    page: int | None = None
    search: dict[str, str | None] = {}


class DecisionFact(EvidenceFact):
    """An attributed value with its subject and retained supporting record references."""

    subject: DecisionRecordReference | None = None
    evidence: tuple[DecisionRecordReference, ...] = ()


class DecisionContext(BaseModel):
    """The immutable facts and record references presented beside a decision form."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    facts: tuple[DecisionFact, ...] = ()
    references: tuple[DecisionRecordReference, ...] = ()

    def records(self) -> tuple[DecisionRecordReference, ...]:
        """Project all authored references, including each fact's subject."""
        return self.references + tuple(
            ref for fact in self.facts
            for ref in ((*((fact.subject,) if fact.subject else ()), *fact.evidence))
        )


class _DefaultRequester:
    """Omission means the admitting actor; explicit None opts out."""


DEFAULT_REQUESTER = _DefaultRequester()


class DecisionRequest(BaseModel):
    """One seat; initial and refine map action values to field values/annotations."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    kind: str = Field(min_length=1, pattern=r"\S")
    subject: Any
    assignees: tuple[Any, ...] | None = Field(min_length=1)
    actions: tuple[type[Action], ...] = Field(min_length=1)
    requester: Any = DEFAULT_REQUESTER
    basis: Any = Field(default_factory=dict)
    context: InstanceOf[DecisionContext] = Field(default_factory=DecisionContext)
    initial: dict[str, dict[str, Any]] = Field(default_factory=dict)
    refine: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    supersede: bool = False
    expires_at: datetime | None = None
    max_attempts: int = Field(default_factory=lambda: settings.ANGEE_DECISION_MAX_ATTEMPTS,
                              strict=True, gt=0, validate_default=True)
    errors: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("actions")
    @classmethod
    def declared_actions(cls, actions: tuple[type[Action], ...]) -> tuple[type[Action], ...]:
        if Action in actions:
            raise ValueError("Every seat needs declared action classes.")
        return actions

    @model_validator(mode="after")
    def subject_for_supersession(self) -> "DecisionRequest":
        if self.supersede and self.subject is None:
            raise ValueError("Supersession requires a subject.")
        return self

    @property
    def attempt_limit(self) -> int:
        """Return the explicit or configured positive number of submission attempts."""
        return self.max_attempts
