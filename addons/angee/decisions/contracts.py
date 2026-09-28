"""Typed requests and the single retained evidence context."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import BaseModel, ConfigDict, JsonValue

from angee.base.identity import instances_from_public_ids
from angee.base.scoping import read_scoped_queryset
from angee.decisions.forms import Action


class DecisionRecordReference(BaseModel):
    """One public record identity and optional navigation hints in retained context."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str
    id: str
    label: str = ""
    tab: str | None = None
    page: int | None = None
    search: dict[str, str | None] = {}


class DecisionFact(BaseModel):
    """An attributed value with its subject and retained supporting record references."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pointer: str
    label: str
    value: JsonValue
    subject: DecisionRecordReference | None = None
    authority: Literal["source", "correction", "unverified"]
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


@dataclass(frozen=True)
class DecisionRequest:
    """One seat; initial and refine map action values to field values/annotations.

    ``assignees=None`` delegates assignment to a consumer's declared REBAC
    relations and requires system admission. An empty explicit assignment is
    invalid. ``replaces`` retains the successor link even for a final answer;
    supersession never rewrites that answer.
    """

    kind: str
    subject: Any
    assignees: tuple[Any, ...] | None
    actions: tuple[type[Action], ...]
    requester: Any = DEFAULT_REQUESTER
    basis: dict[str, Any] = field(default_factory=dict)
    context: DecisionContext = field(default_factory=DecisionContext)
    initial: dict[str, dict[str, Any]] = field(default_factory=dict)
    refine: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    supersede: bool = False
    replaces: Any = None
    expires_at: datetime | None = None
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        """Reject incomplete seats before admission can create any rows."""
        if (not isinstance(self.kind, str) or not self.kind.strip()
            or (self.assignees is not None and (
                not isinstance(self.assignees, Sequence) or isinstance(self.assignees, str) or not self.assignees
            ))):
            raise ValidationError("Every seat needs a kind and assignees.")
        if not self.actions or not all(
            isinstance(action, type) and issubclass(action, Action) and action is not Action for action in self.actions
        ):
            raise ValidationError("Every seat needs declared action classes.")
        if not isinstance(self.context, DecisionContext):
            raise ValidationError("A seat requires a DecisionContext.")
        if self.supersede and self.subject is None:
            raise ValidationError("Supersession requires a subject.")
        if self.replaces is not None and not self.supersede:
            raise ValidationError("A replacement requires supersession.")
        self.attempt_limit

    @property
    def attempt_limit(self) -> int:
        """Return the explicit or configured positive number of submission attempts."""
        attempts = self.max_attempts if self.max_attempts is not None else settings.ANGEE_DECISION_MAX_ATTEMPTS
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise ValidationError("max_attempts must be a positive integer.")
        return attempts


def readable_records(
    refs: tuple[DecisionRecordReference, ...], actors: tuple[Any, ...], *, permission: str = "read",
) -> list[Any]:
    """Check a standing permission in one scoped query per actor and model."""
    grouped: dict[str, set[str]] = {}
    for ref in refs:
        grouped.setdefault(ref.model.lower(), set()).add(ref.id)
    records: list[Any] = []
    for label, ids in sorted(grouped.items()):
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError({"context": "Unknown referenced model."}) from error
        for actor in actors:
            scoped = read_scoped_queryset(model, actor, action=permission)
            if scoped is None:
                raise PermissionDenied("Referenced records require a standing permission.")
            found = instances_from_public_ids(model, ids, queryset=scoped)
            if set(found) != ids:
                raise PermissionDenied("Every participant requires the declared permission on every referenced record.")
        records.extend(found.values())
    return records
