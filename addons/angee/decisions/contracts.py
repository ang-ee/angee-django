"""The proposal and the evidence accompanying one question."""

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from pydantic import BaseModel, ConfigDict, Field, InstanceOf, JsonValue, ValidationInfo, model_validator

from angee.base.evidence import EvidenceFact, EvidenceReference


class DecisionRecordReference(EvidenceReference):
    """A public evidence identity with optional navigation hints."""

    label: str = ""
    tab: str | None = None
    page: int | None = None
    search: dict[str, str | None] = {}


class DecisionFact(EvidenceFact):
    evidence: tuple[DecisionRecordReference, ...] = ()


class DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    facts: tuple[DecisionFact, ...] = ()
    references: tuple[DecisionRecordReference, ...] = ()

    def records(self) -> tuple[DecisionRecordReference, ...]:
        return self.references + tuple(ref for fact in self.facts for ref in fact.evidence)


class SetValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    set: JsonValue


class CallMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    call: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")


class RecordActions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fields: dict[str, SetValue] = Field(default_factory=dict)
    record: CallMethod | None = Field(default=None, exclude_if=lambda value: value is None)


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(min_length=1, pattern=r"\S")
    label: str = Field(min_length=1, pattern=r"\S")
    actions: dict[str, RecordActions] = Field(default_factory=dict)
    outcome: str = Field(min_length=1, pattern=r"\S")


class DecisionProposal(BaseModel):
    """Alternatives are the sole source of actions and continuation outcomes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    multiple: bool = False
    alternatives: tuple[Alternative, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_alternatives(self, info: ValidationInfo) -> "DecisionProposal":
        from angee.base.identity import public_id_for
        from angee.base.refs import canonical_record_model

        keys = [alternative.key for alternative in self.alternatives]
        if len(keys) != len(set(keys)):
            raise ValueError("Alternative keys must be unique.")
        if info.context is None:
            return self
        records = {
            public_id_for(model := canonical_record_model(type(record)), record.pk): record
            for record in info.context["records"]
        }
        for alternative in self.alternatives:
            for identity, actions in alternative.actions.items():
                if identity not in records:
                    raise ValueError(f"Unknown concerned record: {identity}.")
                record = records[identity]
                model = canonical_record_model(type(record))
                for name in actions.fields:
                    try:
                        field = model._meta.get_field(name)
                    except FieldDoesNotExist as error:
                        raise ValueError(f"Unknown field: {name}.") from error
                    if field.name != name or field.primary_key or field.auto_created or not field.editable:
                        raise ValueError(f"Use an editable model field name: {field.name}.")
                    if field.many_to_many or field.one_to_many:
                        raise ValueError(f"Set a scalar field or call its owner's method: {name}.")
                if actions.record and not callable(getattr(model, actions.record.call, None)):
                    raise ValueError(f"Unknown public method: {actions.record.call}.")
        return self

    def choose(self, chosen: Sequence[str]) -> tuple[Alternative, ...]:
        """Validate a verdict and return alternatives in authored order."""
        if isinstance(chosen, (str, bytes)) or not chosen or any(not isinstance(key, str) for key in chosen):
            raise ValueError("Choose at least one alternative.")
        if len(chosen) != len(set(chosen)) or not self.multiple and len(chosen) != 1:
            raise ValueError("Choose distinct alternatives; this proposal permits one unless multiple is true.")
        if set(chosen) - {alternative.key for alternative in self.alternatives}:
            raise ValueError("The verdict contains an unknown alternative.")
        return tuple(alternative for alternative in self.alternatives if alternative.key in chosen)


class _DefaultRequester:
    """Omission uses the asking actor; explicit None allows self-assignment."""


DEFAULT_REQUESTER = _DefaultRequester()


class DecisionRequest(BaseModel):
    """One immutable question for one or more named assignees."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True, revalidate_instances="always")
    kind: str = Field(min_length=1, pattern=r"\S")
    records: tuple[InstanceOf[models.Model], ...] = Field(min_length=1)
    assignees: tuple[Any, ...] = Field(min_length=1)
    proposal: DecisionProposal
    requester: Any = DEFAULT_REQUESTER
    context: InstanceOf[DecisionContext] = Field(default_factory=DecisionContext)

    @model_validator(mode="after")
    def validate_records_and_proposal(self) -> "DecisionRequest":
        if any(record.pk is None or record._state.adding for record in self.records):
            raise ValueError("Concerned records must be saved.")
        DecisionProposal.model_validate(self.proposal.model_dump(mode="json"), context={"records": self.records})
        return self
