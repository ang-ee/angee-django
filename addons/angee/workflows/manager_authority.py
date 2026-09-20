"""Exact transaction-scoped authorities for retained workflow writes."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from angee.base.authority import TransactionBoundContext


@dataclass
class _DefinitionWriteSession:
    """Private state for one locked, reentrant definition transaction."""

    alias: str
    connection_id: int
    workflow_ids: frozenset[int]
    changed_head_ids: set[int]
    copy_target_ids: set[int]


_definition_write_session: ContextVar[_DefinitionWriteSession | None] = ContextVar(
    "workflow_definition_write_session", default=None
)


@dataclass(frozen=True, slots=True)
class _AttemptWriteSession:
    alias: str
    step_run_id: int


_attempt_write_session = TransactionBoundContext[_AttemptWriteSession](
    "workflow_attempt_write_session",
    alias=lambda session: session.alias,
    atomic_error="Attempt writes require an active database transaction.",
    nested_error="Attempt write authority cannot be nested.",
)


@dataclass(slots=True)
class _AtomicWriteCapability:
    """Workflow identity carried inside a shared transaction-bound context."""

    alias: str
    instance_id: int
    consumed: bool = False

    def matches(self, alias: str, instance: Any) -> bool:
        """Match the workflow-owned one-use instance and database identity."""

        return not self.consumed and self.alias == alias and self.instance_id == id(instance)

    def consume(self, alias: str, instance: Any) -> bool:
        if not self.matches(alias, instance):
            return False
        self.consumed = True
        return True


@dataclass(slots=True)
class _AttemptSaveCapability:
    atomic: _AtomicWriteCapability
    step_run_id: int
    pk: Any
    adding: bool


_attempt_save_capability = TransactionBoundContext[_AttemptSaveCapability](
    "workflow_attempt_save_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Attempt saves require an active manager transaction.",
    nested_error="Attempt save authority cannot be nested.",
)


@dataclass(slots=True)
class _StepRunSaveCapability:
    atomic: _AtomicWriteCapability
    run_id: int
    step_run_id: int


_step_run_save_capability = TransactionBoundContext[_StepRunSaveCapability](
    "workflow_step_run_save_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Step-run saves require an active manager transaction.",
    nested_error="Step-run save authority cannot be nested.",
)


@dataclass(slots=True)
class _DecisionResolutionSession:
    alias: str
    decision_id: int
    completed: bool = False


_decision_resolution_session = TransactionBoundContext[_DecisionResolutionSession](
    "workflow_decision_resolution_session",
    alias=lambda session: session.alias,
    atomic_error="Retained decision resolution requires an outer owner transaction.",
    nested_error="Retained decision resolution authority cannot be nested.",
)


@dataclass(slots=True)
class _DecisionSaveCapability:
    atomic: _AtomicWriteCapability
    decision_id: int


_decision_save_capability = TransactionBoundContext[_DecisionSaveCapability](
    "workflow_decision_save_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Decision saves require an active manager transaction.",
    nested_error="Decision save authority cannot be nested.",
)


@dataclass(frozen=True, slots=True)
class _DecisionWriteSession:
    alias: str
    step_run_id: int
    attempt_id: int
    declaration_index: int


_decision_write_session = TransactionBoundContext[_DecisionWriteSession](
    "workflow_decision_write_session",
    alias=lambda session: session.alias,
    atomic_error="Decision creation requires an active manager transaction.",
    nested_error="Decision creation authority cannot be nested.",
)


@dataclass(slots=True)
class _ArtifactWriteCapability:
    atomic: _AtomicWriteCapability
    attempt_id: int
    declaration_index: int


_artifact_write_capability = TransactionBoundContext[_ArtifactWriteCapability](
    "workflow_artifact_write_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Artifact saves require an active manager transaction.",
    nested_error="Artifact save authority cannot be nested.",
)


@dataclass(slots=True)
class _ArtifactBatchCapability:
    atomic: _AtomicWriteCapability
    attempt_id: int
    rows: tuple[tuple[int, int, str], ...]


_artifact_batch_capability = TransactionBoundContext[_ArtifactBatchCapability](
    "workflow_artifact_batch_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Artifact batches require an active manager transaction.",
    nested_error="Artifact batch authority cannot be nested.",
)


def _attempt_write_active(alias: str, step_run_id: int | None = None) -> bool:
    session = _attempt_write_session.get()
    return (
        session is not None and session.alias == alias and (step_run_id is None or session.step_run_id == step_run_id)
    )


def _decision_write_active(alias: str, instance: Any) -> bool:
    session = _decision_write_session.get()
    return (
        session is not None
        and session.alias == alias
        and instance._state.adding
        and instance.step_run_id == session.step_run_id
        and instance.suspension_attempt_id == session.attempt_id
        and instance.declaration_index == session.declaration_index
    )


_test_fixture_write_run: ContextVar[tuple[str, int, int, int] | None] = ContextVar(
    "workflow_test_fixture_write_run", default=None
)


_test_fixture_batch_rows: ContextVar[frozenset[int]] = ContextVar(
    "workflow_test_fixture_batch_rows", default=frozenset()
)


_test_fixture_apply_ids: ContextVar[frozenset[int]] = ContextVar("workflow_test_fixture_apply_ids", default=frozenset())


_recovery_write_run: ContextVar[tuple[str, int, int, int] | None] = ContextVar(
    "workflow_recovery_write_run", default=None
)


_recovery_evidence_batch: ContextVar[frozenset[int]] = ContextVar(
    "workflow_recovery_evidence_batch", default=frozenset()
)


_recovery_evidence_write_row: ContextVar[tuple[int, int, int, int, int] | None] = ContextVar(
    "workflow_recovery_evidence_write_row", default=None
)


@dataclass(slots=True)
class _DispatchSaveCapability:
    atomic: _AtomicWriteCapability
    pk: Any
    adding: bool
    kind: str
    target: tuple[int | None, ...]


_dispatch_save_capability = TransactionBoundContext[_DispatchSaveCapability](
    "workflow_dispatch_save_capability",
    alias=lambda capability: capability.atomic.alias,
    atomic_error="Dispatch saves require an active manager transaction.",
    nested_error="Dispatch save authority cannot be nested.",
)


@dataclass(slots=True)
class _DispatchConsumeSession:
    alias: str
    kind: str
    target_id: int
    generation: int | None
    dispatch_id: int
    lease_token: uuid.UUID | None
    consumed: bool = False


_dispatch_consume_session = TransactionBoundContext[_DispatchConsumeSession](
    "workflow_dispatch_consume_session",
    alias=lambda session: session.alias,
    atomic_error="Dispatch consumption requires the owning database transaction.",
    nested_error="Dispatch consumption authority cannot be nested.",
)
