"""Typed configuration and producer contracts for built-in workflow operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from angee.workflows.attempts import DecisionRecordAccess
from angee.workflows.bindings import BindingNode, is_binding, is_gate_binding_mapping, parse_binding
from angee.workflows.data_contracts import JsonPath, JsonSchemaDict, schema_data_contract
from angee.workflows.decision_actions import ReviewAction, build_decision_action

NonBlankString = Annotated[str, Field(min_length=1)]


class RetryBackoffConfig(BaseModel):
    """Queue delay settings for one retry policy."""

    model_config = ConfigDict(extra="forbid")

    wait: int = Field(default=0, ge=0)
    linear_wait: int = Field(default=0, ge=0)
    exponential_wait: int = Field(default=0, ge=0)


class RetryConfig(BaseModel):
    """Retry count and canonical backoff object."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1)
    backoff: RetryBackoffConfig = Field(default_factory=RetryBackoffConfig)


class WorkflowStepConfig(BaseModel):
    """Common execution settings accepted by every typed workflow operation."""

    model_config = ConfigDict(extra="forbid")

    retry: RetryConfig | None = None


class WaitConfig(WorkflowStepConfig):
    """Timer-wait configuration."""

    until: datetime = Field(description="Date and time when execution may resume.")


class CallWorkflowConfig(WorkflowStepConfig):
    """One pinned, keyed, or input-selected child workflow contract."""

    publication: NonBlankString | None = None
    workflow_key: NonBlankString | None = None
    expected_input_schema: JsonSchemaDict | None = Field(default=None, json_schema_extra={"widget": "json"})
    expected_output_schema: JsonSchemaDict | None = Field(default=None, json_schema_extra={"widget": "json"})
    expected_subject: str | None = None
    expected_outcomes: list[NonBlankString] | None = None

    @model_validator(mode="after")
    def complete_selector(self) -> CallWorkflowConfig:
        """Require either one static publication or one complete stable contract."""

        contract = (
            self.expected_input_schema,
            self.expected_output_schema,
            self.expected_subject,
            self.expected_outcomes,
        )
        if self.publication is not None:
            if self.workflow_key is not None or any(value is not None for value in contract):
                raise ValueError("Static calls cannot also declare a keyed or dynamic contract.")
            return self
        if any(value is None for value in contract):
            raise ValueError("Keyed and input-selected calls require input, output, subject and outcome contracts.")
        assert self.expected_outcomes is not None
        if len(set(self.expected_outcomes)) != len(self.expected_outcomes):
            raise ValueError("Call expected_outcomes must be distinct.")
        return self


class GateSlotConfig(BaseModel):
    """One approval seat in a gate operation."""

    model_config = ConfigDict(extra="forbid")

    assignees: list[NonBlankString] = Field(min_length=1)
    priority: int
    requester: str = ""
    escalation: list[str] | None = None
    action: str = ""
    payload: dict[str, Any] | None = None
    decision_schema: dict[str, Any] | None = None
    target: GateTargetConfig | None = None
    record_access: list[DecisionRecordAccess] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_assignee(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "assignees" not in normalized and "assignee" in normalized:
            assignee = normalized.pop("assignee")
            normalized["assignees"] = [assignee] if isinstance(assignee, str) else assignee
        return normalized

    @field_validator("requester", mode="before")
    @classmethod
    def empty_requester(cls, value: Any) -> Any:
        return "" if value is None else value


class GateTargetConfig(BaseModel):
    """One related record displayed by the gate."""

    model_config = ConfigDict(extra="forbid")

    model: NonBlankString
    id: NonBlankString
    tab: str = ""


GateSlotConfig.model_rebuild()


def _default_slot_priorities(value: Any) -> Any:
    """Use declared seat order when a slot omits its priority."""

    if not isinstance(value, list):
        return value
    return [
        {**slot, "priority": index} if isinstance(slot, dict) and "priority" not in slot else slot
        for index, slot in enumerate(value)
    ]


def _empty_dynamic_object(value: Any) -> Any:
    """Normalize a null dynamic object to the gate's empty mapping."""

    return {} if value is None else value


_GateSlots: TypeAlias = Annotated[list[GateSlotConfig], BeforeValidator(_default_slot_priorities)]
_GateObject: TypeAlias = Annotated[dict[str, Any], BeforeValidator(_empty_dynamic_object)]
_GateTargets: TypeAlias = list[GateTargetConfig]
_GateRecordAccess: TypeAlias = list[DecisionRecordAccess]


class GateBinding(BaseModel):
    """Resolved producer output for the six dynamic fields of a native gate.

    A producer declares ``output_model = GateBinding`` and returns
    ``binding.model_dump(mode="json")`` as its step output. Bind that output into
    the gate's ``input_binding``, then bind each field in ``GateConfig`` through
    ``{"kind": "workflow_input", "path": [field_name]}``.

    These values contain no binding expressions. ``GateBinding(clean=True)``
    permits empty slots: the native gate completes before admitting Decisions.
    Otherwise, the gate requires at least one slot when it admits the result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slots: _GateSlots = Field(default_factory=list)
    payload: _GateObject = Field(default_factory=dict)
    decision_schema: _GateObject = Field(default_factory=dict)
    targets: _GateTargets = Field(default_factory=list)
    record_access: _GateRecordAccess = Field(default_factory=list)
    clean: bool = False


class GateConfig(WorkflowStepConfig):
    """Approval-gate configuration, including explicitly dynamic decision data."""

    policy: Literal["one_done", "all_success", "all_done", "majority", "sequential"] = "one_done"
    action: NonBlankString
    slots: BindingNode | _GateSlots = Field(json_schema_extra={"widget": "json"})
    payload: BindingNode | _GateObject = Field(default_factory=dict, json_schema_extra={"widget": "json"})
    requester: str = ""
    escalation: list[str] = Field(default_factory=list)
    max_attempts: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    escalate_at: datetime | None = None
    decision_schema: BindingNode | _GateObject = Field(default_factory=dict, json_schema_extra={"widget": "json"})
    actions: list[ReviewAction] = Field(default_factory=list)
    properties: dict[NonBlankString, dict[str, Any]] = Field(
        default_factory=dict,
        json_schema_extra={"widget": "json"},
    )
    targets: BindingNode | _GateTargets = Field(default_factory=list, json_schema_extra={"widget": "json"})
    record_access: BindingNode | _GateRecordAccess = Field(default_factory=list, json_schema_extra={"widget": "json"})
    clean: BindingNode | bool = Field(default=False, json_schema_extra={"widget": "json"})
    resume: bool = False

    @model_validator(mode="before")
    @classmethod
    def validate_literal_bindings(cls, value: Any) -> Any:
        """Validate literal producer values at their declared field paths."""

        if isinstance(value, Mapping):
            literals = {
                name: item
                for name, item in value.items()
                if name in GateBinding.model_fields
                and not is_binding(item)
                and not (isinstance(item, Mapping) and "kind" in item)
            }
            GateBinding.model_validate(literals)
        return value

    @field_validator(*GateBinding.model_fields, mode="before")
    @classmethod
    def validate_binding(cls, value: Any) -> Any:
        """Parse every binding-shaped value through the one workflow grammar."""

        if is_gate_binding_mapping(value):
            return parse_binding(value)
        return value

    @field_validator("slots")
    @classmethod
    def valid_static_slots(cls, value: BindingNode | _GateSlots, info: ValidationInfo) -> BindingNode | _GateSlots:
        """Require at least one statically declared slot; bound lists check at runtime."""

        if isinstance(value, list) and not value:
            raise ValueError("Gate slots must contain at least one slot.")
        if not (info.context or {}).get("resolved_bindings") and isinstance(value, list):
            if any(slot.decision_schema is not None and "oneOf" in slot.decision_schema for slot in value):
                raise ValueError("Static gate slots cannot declare hand-written oneOf schemas.")
        return value

    @field_validator("decision_schema")
    @classmethod
    def valid_static_schema(cls, value: BindingNode | _GateObject, info: ValidationInfo) -> BindingNode | _GateObject:
        """Admit action unions only from resolved producers, never static declarations."""

        if not (info.context or {}).get("resolved_bindings") and isinstance(value, dict) and "oneOf" in value:
            raise ValueError("Static gates declare actions, not hand-written oneOf.")
        return value

    @field_validator("policy", mode="before")
    @classmethod
    def empty_policy_uses_default(cls, value: Any) -> Any:
        return "one_done" if value in (None, "") else value

    @field_validator("max_attempts", mode="before")
    @classmethod
    def empty_max_attempts(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("expires_at", "escalate_at", mode="before")
    @classmethod
    def empty_optional_datetime(cls, value: Any) -> Any:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_static_authoring(self) -> GateConfig:
        """Keep fixed action declarations separate from bound or per-slot schemas."""

        has_slot_schema = isinstance(self.slots, list) and any(slot.decision_schema is not None for slot in self.slots)
        if self.actions or self.properties:
            if not isinstance(self.decision_schema, dict) or self.decision_schema or has_slot_schema:
                raise ValueError("Static gate actions and properties cannot be combined with a bound or slot schema.")
            if not self.actions:
                raise ValueError("Static gate properties require actions.")
        return self

    def admission_decision_schema(self) -> dict[str, Any]:
        """Compile fixed actions only when a Decision suspension is admitted."""

        if self.actions:
            property_names = tuple(self.properties)
            actions = tuple(action.model_copy(update={"fields": property_names}) for action in self.actions)
            return build_decision_action(actions=actions, properties=self.properties).decision_schema
        if not isinstance(self.decision_schema, dict):
            raise ValueError("A bound gate decision schema must resolve before Decision admission.")
        return self.decision_schema


class JoinContinuationConfig(WorkflowStepConfig):
    """Contract and bounded reconciliation cadence for a continuation join."""

    child_id_path: JsonPath
    expected_starter_class: NonBlankString
    expected_output_schema: JsonSchemaDict = Field(json_schema_extra={"widget": "json"})
    expected_subject: str = ""
    expected_outcomes: list[NonBlankString] = Field(min_length=1)
    reconcile_after: int = Field(default=900, ge=1, le=3600)

    @field_validator("expected_outcomes")
    @classmethod
    def distinct_outcomes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Join expected_outcomes must be distinct.")
        return value


class ArtifactBindingConfig(BaseModel):
    """One emitted result artifact selected from the projected output."""

    model_config = ConfigDict(extra="forbid")

    model: NonBlankString
    id_path: JsonPath
    label: NonBlankString


class EmitConfig(WorkflowStepConfig):
    """Projection contract and explicit artifact bindings."""

    output_schema: JsonSchemaDict = Field(json_schema_extra={"widget": "json"})
    outcome: NonBlankString = "completed"
    artifacts: list[ArtifactBindingConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_projection(self) -> EmitConfig:
        """Validate result routing and artifact targets once at the config owner."""

        try:
            validate_slug(self.outcome)
        except ValidationError as error:
            raise ValueError("Emit outcome must be a valid slug.") from error
        contract = schema_data_contract(self.output_schema)
        for binding in self.artifacts:
            path = contract.catalogue.resolve_path(binding.id_path)
            node = contract.catalogue.at_path(path) if path is not None else None
            if path is None or not contract.guarantees_path(path) or node is None or node.json_type != "string":
                raise ValueError("Every artifact id_path must be a guaranteed string in output_schema.")
            try:
                apps.get_model(binding.model)
            except (LookupError, ValueError) as error:
                raise ValueError("Every artifact model must be installed.") from error
        return self


class MapConfig(WorkflowStepConfig):
    """Map configuration with an expression-or-literal item source."""

    target_step: NonBlankString
    items: str | list[Any] = Field(json_schema_extra={"widget": "json"})
    min_success_ratio: float | None = Field(default=None, ge=0, le=1)
    all_must_succeed: bool = False

    @field_validator("min_success_ratio", mode="before")
    @classmethod
    def empty_ratio(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("map min_success_ratio must be a number")
        return None if value == "" else value

    @field_validator("items")
    @classmethod
    def non_empty_items(cls, value: str | list[Any]) -> str | list[Any]:
        if not value:
            raise ValueError("map items must be a non-empty expression or list")
        if isinstance(value, str):
            map_items_expression_path(value)
        return value


def map_items_expression_path(value: str) -> tuple[str, tuple[str, ...]]:
    """Parse the established Map expression grammar for authoring and runtime."""

    root, *path = value.split(".")
    if root not in {"subject", "run", "input"}:
        raise ValueError("map items expression must start with subject, run, or input")
    if any(not part for part in path):
        raise ValueError("map items expression path segments cannot be empty")
    return root, tuple(path)
