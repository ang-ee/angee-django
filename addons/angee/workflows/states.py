"""Persisted workflow lifecycle and decision-policy value contracts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from django.db import models


class WorkflowStatus(models.TextChoices):
    """Publication lifecycle for a workflow definition row."""

    DRAFT = "draft", "Draft"
    TEST = "test", "Test"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class WorkflowPurpose(models.TextChoices):
    """Product purpose declared by a workflow lineage."""

    AUTOMATION = "automation", "Automation"
    AGENT_SESSION = "agent_session", "Agent session"


class RunOrigin(models.TextChoices):
    """Caller that created a workflow run."""

    UNKNOWN = "unknown", "Unknown"
    MANUAL = "manual", "Manual"
    TEST = "test", "Test"
    TRIGGER = "trigger", "Trigger"
    SESSION = "session", "Session"
    ERROR_WORKFLOW = "error_workflow", "Error workflow"
    RECOVERY = "recovery", "Recovery"


class WaitingKind(models.TextChoices):
    """Runtime reason a workflow step is waiting."""

    SCHEDULED = "scheduled", "Scheduled"
    APPROVAL = "approval", "Approval"
    EXTERNAL = "external", "External input"
    CHILDREN = "children", "Child steps"


class JoinRule(models.TextChoices):
    """How a step with multiple incoming edges activates over upstream siblings."""

    ALL_SUCCESS = "all_success", "All success"
    ONE_SUCCESS = "one_success", "One success"
    ONE_DONE = "one_done", "One done"
    ALL_DONE = "all_done", "All done"
    NONE_FAILED = "none_failed", "None failed"
    NONE_FAILED_MIN_ONE_SUCCESS = "none_failed_min_one_success", "None failed, at least one success"
    ALWAYS = "always", "Always"


class TriggerKind(models.TextChoices):
    """How a workflow lineage is started."""

    MANUAL = "manual", "Manual"
    EVENT = "event", "Event"
    SCHEDULE = "schedule", "Schedule"


class RunStatus(models.TextChoices):
    """Execution lifecycle for one pinned workflow run."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class StepRunStatus(models.TextChoices):
    """Execution lifecycle for one step-run journal row."""

    SCHEDULED = "scheduled", "Scheduled"
    STARTED = "started", "Started"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    SKIPPED = "skipped", "Skipped"


class Verdict(models.TextChoices):
    """Resolution lifecycle for one awaited decision slot."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    ESCALATED = "escalated", "Escalated"
    EXPIRED = "expired", "Expired"


@dataclass(frozen=True, slots=True)
class DecisionGate:
    """Parsed decision policy for one suspension journal row."""

    policy: str = "one_done"

    @classmethod
    def from_resume_state(cls, state: object) -> DecisionGate:
        gate = state.get("gate") if isinstance(state, Mapping) else None
        policy = str(gate.get("policy", "one_done") or "one_done") if isinstance(gate, Mapping) else "one_done"
        return cls(policy=policy)

    @property
    def is_sequential(self) -> bool:
        return self.policy == "sequential"

    def outcome(self, decisions: Collection[Any]) -> str | None:
        """Derive the authoritative result from a complete declared decision set."""

        terminal = [decision for decision in decisions if decision.verdict in Verdict.TERMINAL]
        if not decisions or not terminal:
            return None
        if self.policy == "one_done":
            return str(terminal[0].verdict.value)
        if self.policy == "all_success":
            for verdict in (Verdict.REJECTED, Verdict.ESCALATED, Verdict.EXPIRED):
                if any(decision.verdict == verdict for decision in terminal):
                    return str(verdict)
            return "completed" if len(terminal) == len(decisions) else None
        if self.policy == "all_done":
            return "completed" if len(terminal) == len(decisions) else None
        if self.policy == "majority":
            for verdict in (Verdict.ESCALATED, Verdict.EXPIRED):
                if any(decision.verdict == verdict for decision in terminal):
                    return str(verdict)
            threshold = len(decisions) // 2 + 1
            completed = sum(decision.verdict == Verdict.COMPLETED for decision in terminal)
            rejected = sum(decision.verdict == Verdict.REJECTED for decision in terminal)
            if completed >= threshold:
                return "completed"
            if rejected >= threshold:
                return "rejected"
            return "rejected" if len(terminal) == len(decisions) else None
        if self.is_sequential:
            for decision in terminal:
                if decision.verdict != Verdict.COMPLETED:
                    return str(decision.verdict.value)
            return "completed" if len(terminal) == len(decisions) else None
        return None

RunStatus.TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED})
StepRunStatus.TERMINAL = frozenset(
    {StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED}
)
StepRunStatus.ACTIVE = frozenset({StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING})
Verdict.TERMINAL = frozenset({Verdict.COMPLETED, Verdict.REJECTED, Verdict.ESCALATED, Verdict.EXPIRED})
CURRENT_PUBLICATION_STATUSES = (WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED)
