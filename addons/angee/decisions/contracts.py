"""The proposal and the evidence accompanying one question."""

from collections.abc import Sequence
from inspect import Parameter, signature
from typing import Any

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, Field, InstanceOf, JsonValue, ValidationInfo, model_validator
from rebac import current_actor

from angee.base.evidence import EvidenceFact, EvidenceReference
from angee.base.identity import instance_from_public_id, public_id_for
from angee.base.refs import canonical_record_model


class DecisionRecordReference(EvidenceReference):
    """A public evidence identity with optional navigation hints."""

    label: str = ""
    tab: str | None = None
    page: int | None = None
    search: dict[str, str | None] = {}


class DecisionFact(EvidenceFact):
    evidence: tuple[DecisionRecordReference, ...] = ()
    widget: str | None = None
    row: dict[str, JsonValue] = Field(default_factory=dict)


class DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reason: str = ""
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
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class RecordActions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    fields: dict[str, SetValue] = Field(default_factory=dict)
    record: CallMethod | None = Field(default=None, exclude_if=lambda value: value is None)

    def target_model(self, record: models.Model) -> type[models.Model]:
        """Keep canonical identity while naming a concrete action owner."""
        canonical = canonical_record_model(type(record))
        try:
            target = apps.get_model(self.model) if self.model else canonical
        except (LookupError, ValueError) as error:
            raise ValueError("Unknown action model.") from error
        if target._meta.abstract or canonical_record_model(target) is not canonical:
            raise ValueError("The action model must share the concerned record's canonical identity.")
        return target

    def resolve(self, record: models.Model, *, context: dict[str, Any]) -> dict[str, Any]:
        """Validate the action owner and decode writes through its model fields."""
        model = self.target_model(record)
        if not isinstance(record, model):
            raise ValueError("Concern the concrete record named by the action model.")
        values = {}
        for name, operation in self.fields.items():
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist as error:
                raise ValueError(f"Unknown field: {name}.") from error
            if (field.auto_created or not field.concrete or field.name != name
                    or field.primary_key or not field.editable):
                raise ValueError(f"Use an editable model field name: {field.name}.")
            if field.many_to_many or field.one_to_many:
                raise ValueError(f"Set a scalar field or call its owner's method: {name}.")
            value = operation.set
            try:
                if field.is_relation:
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f"Use a related record public id or null for {name}.")
                    if value is None and not field.null:
                        raise ValueError(f"The field {name} cannot be null.")
                    # Request construction validates shape; admission/application supply the read actor.
                    if "actor" not in context:
                        continue
                    value = None if value is None else instance_from_public_id(
                        field.related_model, value,
                        queryset=field.related_model.objects.with_actor(context.get("actor", current_actor())),
                    )
                    if value is None and operation.set is not None:
                        raise ValueError(f"The related record for {name} is absent or unreadable.")
                    if value is not None:
                        context.get("related_records", []).append(value)
                        field.run_validators(field.to_python(value.pk))
                else:
                    value = field.clean(value, record)
            except (ValidationError, TypeError) as error:
                raise ValueError(f"Invalid proposed value for {name}: {error}") from error
            values[name] = value
        if self.record:
            call = self.record.call
            if call == "delete" or call not in getattr(model, "decision_methods", ()):
                raise ValueError(f"Undeclared decision method: {call}.")
            bound = getattr(record, call, None)
            if not callable(bound):
                raise ValueError(f"Unknown decision method: {call}.")
            parameters = tuple(signature(bound).parameters.values())
            if any(parameter.default is Parameter.empty and parameter.kind not in {
                Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD,
            } for parameter in parameters):
                raise ValueError(f"Decision method {call} takes required arguments.")
            try:
                signature(bound).bind(**self.record.arguments)
            except TypeError as error:
                raise ValueError(f"Invalid arguments for {call}: {error}") from error
        return values


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
        keys = [alternative.key for alternative in self.alternatives]
        if len(keys) != len(set(keys)):
            raise ValueError("Alternative keys must be unique.")
        if info.context is None:
            return self
        records = {
            public_id_for(canonical_record_model(type(record)), record.pk): record
            for record in info.context["records"]
        }
        for alternative in self.alternatives:
            for identity, actions in alternative.actions.items():
                if identity not in records:
                    raise ValueError(f"Unknown concerned record: {identity}.")
                actions.resolve(records[identity], context=info.context)
        return self

    def choose(self, chosen: Sequence[str]) -> tuple[Alternative, ...]:
        """Validate a verdict and return alternatives in authored order."""
        if isinstance(chosen, (str, bytes)) or not chosen or any(not isinstance(key, str) for key in chosen):
            raise ValueError("Choose at least one alternative.")
        if len(chosen) != len(set(chosen)) or not self.multiple and len(chosen) != 1:
            raise ValueError("Choose distinct alternatives; this proposal permits one unless multiple is true.")
        if set(chosen) - {alternative.key for alternative in self.alternatives}:
            raise ValueError("The verdict contains an unknown alternative.")
        selected = tuple(alternative for alternative in self.alternatives if alternative.key in chosen)
        written: set[tuple[str, str]] = set()
        for alternative in selected:
            for identity, actions in alternative.actions.items():
                for name in actions.fields:
                    if (identity, name) in written:
                        raise ValueError(f"Choose only one value for {name.replace('_', ' ')}.")
                    written.add((identity, name))
        return selected


class DecisionRequest(BaseModel):
    """One immutable question for one or more named assignees."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True, revalidate_instances="always")
    kind: str = Field(min_length=1, pattern=r"\S")
    records: tuple[InstanceOf[models.Model], ...] = Field(min_length=1)
    assignees: tuple[Any, ...] = Field(min_length=1)
    proposal: DecisionProposal
    requester: Any = None
    context: InstanceOf[DecisionContext] = Field(default_factory=DecisionContext)

    @model_validator(mode="after")
    def validate_records_and_proposal(self) -> "DecisionRequest":
        if any(record.pk is None or record._state.adding for record in self.records):
            raise ValueError("Concerned records must be saved.")
        DecisionProposal.model_validate(self.proposal.model_dump(mode="json"), context={"records": self.records})
        return self
