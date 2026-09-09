"""Typed admission contracts shared by workflow test persistence and APIs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models

from angee.workflows.attempts import JsonPresence, validate_json_presence

if TYPE_CHECKING:
    from angee.workflows.graph import (
        GraphDiagnostic,
        GraphFreshnessReason,
        GraphTestFixtureRequirement,
        GraphTestOperation,
    )


class TestScope(models.TextChoices):
    """Execution closure requested for an immutable workflow test snapshot."""

    WHOLE = "whole", "Whole workflow"
    NODE = "node", "Selected node"


class TestFixtureRole(models.TextChoices):
    """How one retained test fixture participates in test execution."""

    OUTPUT = "output", "Operation output"
    MAP_ITEM = "map_item", "Current Map item"


@dataclass(frozen=True, slots=True)
class TestFixtureSpec:
    """Exact admission request for one manual or captured test fixture."""

    step_key: str
    role: TestFixtureRole
    value: JsonPresence = JsonPresence()
    item_index: int | None = None
    outcome: str = ""
    captured_attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class TestFixtureSourceSummary:
    """Bounded retained-source metadata; payload values are fetched separately."""

    attempt_id: str
    run_id: str
    workflow_id: str
    workflow_revision: int
    step_id: str
    step_key: str
    role: TestFixtureRole
    item_index: int | None
    outcome: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class TestFixtureSource:
    """One revalidated captured source including its exact selected payload."""

    summary: TestFixtureSourceSummary
    value: JsonPresence


@dataclass(frozen=True, slots=True)
class TestFixtureSourcePage:
    """One bounded page of authorized capture summaries."""

    items: tuple[TestFixtureSourceSummary, ...]
    next_after: str | None


@dataclass(frozen=True, slots=True)
class WorkflowTestSetupPlan:
    """Transport-neutral authoritative setup projection for one saved revision."""

    source_step_id: str | None
    snapshot_step_id: str | None
    operations: tuple[GraphTestOperation, ...]
    required_fixtures: tuple[GraphTestFixtureRequirement, ...]
    diagnostics: tuple[GraphDiagnostic, ...]
    requires_map_item: bool
    freshness: tuple[GraphFreshnessReason, ...] = ()

    @property
    def is_current(self) -> bool:
        """Return whether executable semantics still match the lineage head."""

        return not self.freshness


@dataclass(frozen=True, slots=True)
class WorkflowTestRepairContext:
    """Authorized identities and original run inputs for testing a draft repair."""

    source_attempt_id: str
    source_run_id: str
    source_workflow_id: str
    source_revision: int
    draft_workflow_id: str
    draft_revision: int
    source_step_key: str
    source_step_id: str
    current_source_step_id: str | None
    subject: object | None
    input: JsonPresence
    fixtures: tuple[TestFixtureSourceSummary, ...]


def validate_test_fixture_spec(spec: TestFixtureSpec) -> TestFixtureSpec:
    """Validate fixture scalar and exact JSON facts before graph admission."""

    if not isinstance(spec, TestFixtureSpec) or type(spec.step_key) is not str or not spec.step_key:
        raise ValueError("Fixture step keys must be non-empty strings.")
    if not isinstance(spec.role, TestFixtureRole):
        raise ValueError("Fixtures require a declared role.")
    if spec.item_index is not None and (type(spec.item_index) is not int or spec.item_index < 0):
        raise ValueError("Fixture item indexes must be non-negative integers.")
    if spec.role == TestFixtureRole.MAP_ITEM and spec.item_index is None:
        raise ValueError("Map item fixtures require an exact item index.")
    if spec.role == TestFixtureRole.MAP_ITEM and spec.captured_attempt_id is None and not spec.value.present:
        raise ValueError("Map item fixtures require a present raw item value.")
    if type(spec.outcome) is not str:
        raise ValueError("Fixture outcomes must be strings.")
    if spec.captured_attempt_id is not None and (
        type(spec.captured_attempt_id) is not str or not spec.captured_attempt_id
    ):
        raise ValueError("Captured fixture attempts require a non-empty identity.")
    validate_json_presence(spec.value, label="test fixture value")
    if spec.captured_attempt_id is not None and (spec.value.present or spec.outcome):
        raise ValueError("Captured fixtures derive value and outcome from retained evidence.")
    return spec
