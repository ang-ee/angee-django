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

    @field_validator("backoff", mode="before")
    @classmethod
    def scalar_backoff_is_wait(cls, value: Any) -> Any:
        """Normalize the legacy scalar delay without changing its meaning."""

        return {"wait": value} if not isinstance(value, dict) else value


class WorkflowStepConfig(BaseModel):
    """Common execution settings accepted by every typed workflow operation."""

    model_config = ConfigDict(extra="forbid")

    retry: RetryConfig | None = None

    @field_validator("retry", mode="before")
    @classmethod
    def empty_retry_is_absent(cls, value: Any) -> Any:
        """Preserve the legacy empty retry spellings as no retry override."""

        return None if value in (None, "", False) else value


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
    def normalize_legacy_assignees(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        legacy_assignees = normalized.pop("assignees", None)
        if normalized.get("slots") is None and legacy_assignees is not None:
            normalized.pop("slots", None)
            normalized["slots"] = [
                {"assignees": [subject]} for subject in legacy_assignees
            ]
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
    """Map configuration with its legacy expression-or-literal item source."""

    target_step: NonBlankString
    items: str | list[Any] = Field(json_schema_extra={"widget": "json"})
    min_success_ratio: float | None = Field(default=None, ge=0, le=1)
    all_must_succeed: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_min_success(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        legacy_ratio = normalized.pop("min_success", None)
        if "min_success_ratio" not in normalized and legacy_ratio is not None:
            normalized["min_success_ratio"] = legacy_ratio
        return normalized

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
        return value
