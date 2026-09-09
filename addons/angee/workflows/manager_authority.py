"""Exact transaction-scoped authorities for retained workflow writes."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from django.db import connections


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
    connection_id: int
    step_run_id: int


_attempt_write_session: ContextVar[_AttemptWriteSession | None] = ContextVar(
    "workflow_attempt_write_session", default=None
)


@dataclass(slots=True)
class _AtomicWriteCapability:
    """One-use authority bound to an exact instance and outer transaction."""

    alias: str
    connection_id: int
    outer_atomic_id: int
    instance_id: int
    consumed: bool = False

    def matches(self, alias: str, instance: Any) -> bool:
        connection = connections[alias]
        return (
            not self.consumed
            and self.alias == alias
            and self.connection_id == id(connection)
            and connection.in_atomic_block
            and bool(connection.atomic_blocks)
            and self.outer_atomic_id == id(connection.atomic_blocks[0])
            and self.instance_id == id(instance)
        )

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


_attempt_save_capability: ContextVar[_AttemptSaveCapability | None] = ContextVar(
    "workflow_attempt_save_capability", default=None
)


@dataclass(slots=True)
class _StepRunSaveCapability:
    atomic: _AtomicWriteCapability
    run_id: int
    step_run_id: int


_step_run_save_capability: ContextVar[_StepRunSaveCapability | None] = ContextVar(
    "workflow_step_run_save_capability", default=None
)


@dataclass(slots=True)
class _DecisionResolutionSession:
    alias: str
    connection_id: int
    outer_atomic_id: int
    decision_id: int
    completed: bool = False


_decision_resolution_session: ContextVar[_DecisionResolutionSession | None] = ContextVar(
    "workflow_decision_resolution_session", default=None
)


@dataclass(slots=True)
class _DecisionSaveCapability:
    atomic: _AtomicWriteCapability
    decision_id: int


_decision_save_capability: ContextVar[_DecisionSaveCapability | None] = ContextVar(
    "workflow_decision_save_capability", default=None
)


@dataclass(frozen=True, slots=True)
class _DecisionWriteSession:
    alias: str
    connection_id: int
    step_run_id: int
    attempt_id: int
    declaration_index: int


_decision_write_session: ContextVar[_DecisionWriteSession | None] = ContextVar(
    "workflow_decision_write_session", default=None
)


@dataclass(slots=True)
class _ArtifactWriteCapability:
    atomic: _AtomicWriteCapability
    attempt_id: int
    declaration_index: int


_artifact_write_capability: ContextVar[_ArtifactWriteCapability | None] = ContextVar(
    "workflow_artifact_write_capability", default=None
)


@dataclass(slots=True)
class _ArtifactBatchCapability:
    atomic: _AtomicWriteCapability
    attempt_id: int
    rows: tuple[tuple[int, int, str], ...]


_artifact_batch_capability: ContextVar[_ArtifactBatchCapability | None] = ContextVar(
    "workflow_artifact_batch_capability", default=None
)


def _attempt_write_active(alias: str, step_run_id: int | None = None) -> bool:
    session = _attempt_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
        and (step_run_id is None or session.step_run_id == step_run_id)
    )


def _decision_write_active(alias: str, instance: Any) -> bool:
    session = _decision_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
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


_test_fixture_apply_ids: ContextVar[frozenset[int]] = ContextVar(
    "workflow_test_fixture_apply_ids", default=frozenset()
)


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
    target: tuple[int | None, int | None, int | None, int | None]


_dispatch_save_capability: ContextVar[_DispatchSaveCapability | None] = ContextVar(
    "workflow_dispatch_save_capability", default=None
)


@dataclass(slots=True)
class _DispatchConsumeSession:
    alias: str
    connection_id: int
    outer_atomic_id: int
    kind: str
    target_id: int
    generation: int | None
    dispatch_id: int
    lease_token: uuid.UUID | None
    consumed: bool = False


_dispatch_consume_session: ContextVar[_DispatchConsumeSession | None] = ContextVar(
    "workflow_dispatch_consume_session", default=None
)
