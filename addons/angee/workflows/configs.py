"""Typed configuration contracts for built-in workflow operations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class GateSlotConfig(BaseModel):
    """One approval seat in a gate operation."""

    model_config = ConfigDict(extra="forbid")

    assignees: list[NonBlankString] = Field(min_length=1)
    priority: int
    requester: str = ""
    escalation: list[str] | None = None

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


class GateConfig(WorkflowStepConfig):
    """Approval-gate configuration, including explicitly dynamic decision data."""

    policy: Literal["one_done", "all_success", "majority", "sequential"] = "one_done"
    action: NonBlankString
    slots: list[GateSlotConfig] = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})
    requester: str = ""
    escalation: list[str] = Field(default_factory=list)
    max_attempts: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    escalate_at: datetime | None = None
    decision_schema: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})

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
