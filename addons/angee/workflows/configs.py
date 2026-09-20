"""Typed configuration contracts for built-in workflow operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from angee.workflows.attempts import DecisionRecordAccess
from angee.workflows.bindings import BindingNode, parse_binding
from angee.workflows.data_contracts import JsonPath, JsonSchemaDict, schema_data_contract
from angee.workflows.decision_actions import ReviewAction, build_decision_action

NonBlankString = Annotated[str, Field(min_length=1)]
_GATE_BINDING_KINDS = frozenset({"constant", "workflow_input", "step_output", "map_item", "object", "array"})


def is_gate_binding_mapping(value: Any) -> bool:
    """Identify gate bindings while rejecting the reserved discriminator on literals."""

    if not isinstance(value, Mapping) or "kind" not in value:
        return False
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _GATE_BINDING_KINDS:
        raise ValueError("The top-level key 'kind' is reserved for workflow bindings in gate mapping fields.")
    return True


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
    """One related record and any admitted prior-Decision authority paths."""

    model_config = ConfigDict(extra="forbid")

    model: NonBlankString
    id: NonBlankString
    tab: str = ""
    authority_path: tuple[str | int, ...] = ()
    authority_gate_path: tuple[str | int, ...] = ()

    @field_validator("authority_path", "authority_gate_path", mode="before")
    @classmethod
    def typed_path(cls, value: Any) -> Any:
        """Reject coercive or empty path segments before they become authority."""

        if not isinstance(value, list | tuple) or any(
            type(part) not in {str, int} or (isinstance(part, str) and not part) for part in value
        ):
            raise ValueError("Gate target authority paths require typed non-empty segments.")
        return value

    @model_validator(mode="after")
    def complete_authority(self) -> GateTargetConfig:
        """Require the original gate path only for a forwarded proposal authority."""

        if self.authority_gate_path and not self.authority_path:
            raise ValueError("Gate target authority_gate_path requires authority_path.")
        return self


GateSlotConfig.model_rebuild()


class GateConfig(WorkflowStepConfig):
    """Approval-gate configuration, including explicitly dynamic decision data."""

    policy: Literal["one_done", "all_success", "all_done", "majority", "sequential"] = "one_done"
    action: NonBlankString
    slots: BindingNode | list[GateSlotConfig] = Field(json_schema_extra={"widget": "json"})
    payload: BindingNode | dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})
    requester: str = ""
    escalation: list[str] = Field(default_factory=list)
    max_attempts: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    escalate_at: datetime | None = None
    decision_schema: BindingNode | dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})
    actions: list[ReviewAction] = Field(default_factory=list)
    properties: dict[NonBlankString, dict[str, Any]] = Field(
        default_factory=dict,
        json_schema_extra={"widget": "json"},
    )
    targets: BindingNode | list[GateTargetConfig] = Field(default_factory=list, json_schema_extra={"widget": "json"})
    record_access: BindingNode | list[DecisionRecordAccess] = Field(
        default_factory=list, json_schema_extra={"widget": "json"}
    )
    clean: BindingNode | bool = Field(default=False, json_schema_extra={"widget": "json"})
    resume: bool = False

    @model_validator(mode="before")
    @classmethod
    def default_slot_priorities(cls, value: Any) -> Any:
        """Use declared seat order when a slot omits its priority."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        slots = normalized.get("slots")
        if isinstance(slots, list):
            normalized["slots"] = [
                {**slot, "priority": index} if isinstance(slot, dict) and "priority" not in slot else slot
                for index, slot in enumerate(slots)
            ]
        return normalized

    @field_validator(
        "slots",
        "payload",
        "decision_schema",
        "targets",
        "record_access",
        "clean",
        mode="before",
    )
    @classmethod
    def validate_binding(cls, value: Any) -> Any:
        """Parse every binding-shaped value through the one workflow grammar."""

        if is_gate_binding_mapping(value):
            return parse_binding(value)
        return value

    @field_validator("slots")
    @classmethod
    def non_empty_static_slots(cls, value: BindingNode | list[GateSlotConfig]) -> BindingNode | list[GateSlotConfig]:
        """Require at least one statically declared slot; bound lists check at runtime."""

        if isinstance(value, list) and not value:
            raise ValueError("Gate slots must contain at least one slot.")
        return value

    @field_validator("policy", mode="before")
    @classmethod
    def empty_policy_uses_default(cls, value: Any) -> Any:
        return "one_done" if value in (None, "") else value

    @field_validator("payload", "decision_schema", mode="before")
    @classmethod
    def empty_dynamic_object(cls, value: Any) -> Any:
        return {} if value is None else value

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

        has_slot_schema = isinstance(self.slots, list) and any(
            slot.decision_schema is not None for slot in self.slots
        )
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
            node = contract.catalogue.at_path(binding.id_path)
            if not contract.guarantees_path(binding.id_path) or node is None or node.json_type != "string":
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
