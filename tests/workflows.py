"""Shared workflow test models, tables, and synchronous engine helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections, models, router
from rebac import RelationshipTuple, system_context, to_subject_ref, write_relationships
from rebac.backends.local import LocalBackend
from rebac.models import PermissionAuditEvent
from rebac.resources import to_object_ref

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.models import (
    Edge as AbstractEdge,
)
from angee.workflows.models import (
    Step as AbstractStep,
)
from angee.workflows.models import (
    Trigger as AbstractTrigger,
)
from angee.workflows.models import (
    Workflow as AbstractWorkflow,
)
from angee.workflows.steps import StepImpl, StepResult
from angee.workflows.testing import advance_once as advance_once
from angee.workflows.testing import execute_started as execute_started
from angee.workflows.testing import owned_run as owned_run
from angee.workflows.testing import run_to_terminal as run_to_terminal
from angee.workflows.testing import start_run as start_workflow_run
from angee.workflows.testing import step_run_for as step_run_for
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.test_transitions import TransitionRouter

_native_authorization = ContextVar("workflow_test_native_authorization", default=False)


def reject_default_domain_query(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
    """Permit only the documented native REBAC audit insert on a conflicting DB."""

    audit_table = connection.ops.quote_name(PermissionAuditEvent._meta.db_table)
    if sql.startswith(f"INSERT INTO {audit_table} "):
        return execute(sql, params, many, context)
    raise AssertionError(f"Workflow operation queried the conflicting default alias: {sql}")


class WorkflowWriteRouter(TransitionRouter):
    """Reject unbound workflow reads, preserving the documented REBAC frontier."""

    def db_for_read(self, model: type[models.Model], **hints: Any) -> str:
        if model._meta.app_label == "rebac" or _native_authorization.get():
            return "default"
        return super().db_for_read(model, **hints)

    def db_for_write(self, model: type[models.Model], **hints: Any) -> str:
        if model._meta.app_label == "rebac":
            return "default"
        return super().db_for_write(model, **hints)

    def allow_relation(self, obj1: models.Model, obj2: models.Model, **hints: Any) -> bool | None:
        """Permit fixture aliases of the same physical database to share FKs."""

        if obj1._state.db is None or obj2._state.db is None:
            return None
        left = connections[obj1._state.db].settings_dict
        right = connections[obj2._state.db].settings_dict
        return all(left[key] == right[key] for key in ("ENGINE", "NAME", "HOST", "PORT"))


@pytest.fixture
def workflow_authorization_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep native admission's unbound field reads on its documented default DB."""

    def native_entry(operation: Any) -> Any:
        @wraps(operation)
        def native_check(*args: Any, **kwargs: Any) -> Any:
            token = _native_authorization.set(True)
            try:
                return operation(*args, **kwargs)
            finally:
                _native_authorization.reset(token)

        return native_check

    for name in ("check_access", "accessible"):
        monkeypatch.setattr(LocalBackend, name, native_entry(getattr(LocalBackend, name)))


@pytest.fixture
def workflow_audit_frontier(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Observe native default-only audit emissions while SQLite's writer is locked.

    Native REBAC audit persistence has no operation-alias contract. Its second
    connection cannot write the same SQLite file during a workflow transaction;
    these alias tests retain complete emissions at that documented frontier.
    """

    manager_type = type(PermissionAuditEvent.objects)
    create = manager_type.create
    emitted: list[dict[str, Any]] = []

    def retain_audit(manager: Any, **kwargs: Any) -> Any:
        if manager.model is not PermissionAuditEvent:
            return create(manager, **kwargs)
        assert manager._db is None
        assert router.db_for_write(PermissionAuditEvent) == "default"
        assert set(kwargs) == {
            "kind", "actor_subject_type", "actor_subject_id", "target_repr", "before", "after", "reason",
        }
        emitted.append(dict(kwargs))
        return PermissionAuditEvent(**kwargs)

    monkeypatch.setattr(manager_type, "create", retain_audit)
    yield emitted
    assert any(event["kind"] == PermissionAuditEvent.KIND_SUDO_BYPASS for event in emitted)


class Workflow(AbstractWorkflow):
    """Concrete workflow model for source-addon tests."""

    rebac_grantable = AbstractWorkflow.rebac_grantable

    class Meta(AbstractWorkflow.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow"
        rebac_resource_type = "workflows/workflow"


class Step(AbstractStep):
    """Concrete workflow step model for source-addon tests."""

    class Meta(AbstractStep.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step"
        rebac_resource_type = "workflows/step"


class Edge(AbstractEdge):
    """Concrete workflow edge model for source-addon tests."""

    class Meta(AbstractEdge.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_edge"
        rebac_resource_type = "workflows/edge"


class Trigger(AbstractTrigger):
    """Concrete workflow trigger model for source-addon tests."""

    class Meta(AbstractTrigger.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_trigger"
        rebac_resource_type = "workflows/trigger"


class WorkflowRun(workflow_models.WorkflowRun):
    """Concrete workflow run model for source-addon engine tests."""

    rebac_grantable = workflow_models.WorkflowRun.rebac_grantable

    class Meta(workflow_models.WorkflowRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow_run"
        rebac_resource_type = "workflows/run"


class StepRun(workflow_models.StepRun):
    """Concrete workflow step-run journal model for source-addon engine tests."""

    class Meta(workflow_models.StepRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_run"
        rebac_resource_type = "workflows/step_run"


class StepAttempt(workflow_models.StepAttempt):
    """Concrete retained attempt model for source-addon runtime tests."""

    class Meta(workflow_models.StepAttempt.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_attempt"
        rebac_resource_type = "workflows/step_attempt"


class StepExternalSubscription(workflow_models.StepExternalSubscription):
    """Concrete attempt-owned external target for workflow engine tests."""

    class Meta(workflow_models.StepExternalSubscription.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_external_subscription"


class StepArtifact(workflow_models.StepArtifact):
    """Concrete explicit result artifact model for source-addon runtime tests."""

    class Meta(workflow_models.StepArtifact.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_artifact"


class WorkflowTestFixture(workflow_models.WorkflowTestFixture):
    """Concrete retained workflow test fixture model."""

    class Meta(workflow_models.WorkflowTestFixture.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_test_fixture"


class WorkflowRecoveryEvidence(workflow_models.WorkflowRecoveryEvidence):
    """Concrete retained recovery evidence model."""

    class Meta(workflow_models.WorkflowRecoveryEvidence.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_recovery_evidence"


class Decision(workflow_models.Decision):
    """Concrete decision model for source-addon runtime tests."""

    rebac_grantable = {"reader": "share"}

    class Meta(workflow_models.Decision.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_decision"
        rebac_resource_type = "workflows/decision"


class FixtureStep(StepImpl):
    """Concrete configurable operation used only by workflow runtime tests."""

    key = "fixture"
    label = "Fixture"
    category = "Tests"
    selectable = False
    deterministic = False

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        """Return the explicitly configured test output and outcome."""

        del self, now
        config = dict(step_run.step.config)
        if config.get("mode") == "error":
            raise RuntimeError(str(config.get("error", "fixture failed")))
        return StepResult.done(
            output={
                "key": step_run.step.key,
                "input": step_run.input,
                **dict(config.get("output", {})),
            },
            outcome=str(config.get("outcome", "done")),
        )


class WorkflowDispatch(workflow_models.WorkflowDispatch):
    """Concrete durable dispatch model for source-addon runtime tests."""

    class Meta(workflow_models.WorkflowDispatch.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_dispatch"


WORKFLOW_DEFINITION_MODELS = (Workflow, Step, Edge, Trigger)
WORKFLOW_RUNTIME_MODELS = (
    *WORKFLOW_DEFINITION_MODELS,
    WorkflowRun,
    StepRun,
    StepAttempt,
    StepExternalSubscription,
    StepArtifact,
    WorkflowTestFixture,
    WorkflowRecoveryEvidence,
    Decision,
    WorkflowDispatch,
)


@contextmanager
def workflow_table_setup(models: tuple[type[Any], ...]) -> Iterator[None]:
    """Create missing workflow tables, sync permissions, clear rows, then drop created tables."""

    created = _create_missing_tables(models)
    call_command("rebac", "sync", verbosity=0)
    _clear_model_tables(models)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def workflow_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete workflow definition tables."""

    del transactional_db
    with workflow_table_setup(WORKFLOW_DEFINITION_MODELS):
        yield


@pytest.fixture()
def workflow_engine_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete workflow runtime tables."""

    del transactional_db
    with workflow_table_setup(WORKFLOW_RUNTIME_MODELS):
        yield


@pytest.fixture()
def workflow_gate_tables(workflow_engine_tables: None) -> None:
    """Alias runtime tables for gate tests."""

    del workflow_engine_tables


@pytest.fixture()
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow tests synchronous by replacing queue enqueue hooks."""

    from angee.workflows import dispatch

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id, **kwargs: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when, **kwargs: None)
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: None)
    monkeypatch.setattr(dispatch, "enqueue_task", lambda *args, **kwargs: None)


def workflow_actor() -> Any:
    """Return the ordinary author used by source workflow graph fixtures."""

    with system_context(reason="test workflow fixture author"):
        return get_user_model().objects.get_or_create(username="workflow-fixture-author")[0]


def admit_workflow_actor(workflow: Any, actor: Any = None) -> Any:
    """Give one fixture actor the workflow edit permission needed to start it."""

    actor = actor if actor is not None else workflow_actor()
    with system_context(reason="test workflow action admission"):
        head = workflow.published_from if workflow.published_from_id is not None else workflow
        write_relationships([RelationshipTuple(to_object_ref(head), "editor", to_subject_ref(actor))])
    return actor


def start_run(workflow: Any, *, subject: Any = None, actor: Any = None) -> Any:
    """Execute source fixtures as an explicitly authorized test actor."""

    return start_workflow_run(workflow, subject=subject, actor=admit_workflow_actor(workflow, actor))


def workflow_with_steps(
    *,
    actor: Any = None,
    name: str = "Engine",
    key: str = "",
    purpose: workflow_models.WorkflowPurpose = workflow_models.WorkflowPurpose.AUTOMATION,
    subject_declaration: str = "",
    max_steps: int = 1000,
    budget: dict[str, Any] | None = None,
    steps: tuple[dict[str, Any], ...],
    edges: tuple[tuple[str, str, str], ...],
) -> Workflow:
    """Create and publish a workflow definition graph."""

    with system_context(reason="test workflows definition"):
        draft = Workflow.objects.create(
            created_by=actor if actor is not None else workflow_actor(),
            key=key,
            name=name,
            purpose=purpose,
            subject_declaration=subject_declaration,
            max_steps=max_steps,
            budget=budget or {},
        )
        by_key = {}
        for index, spec in enumerate(steps):
            by_key[spec["key"]] = Step.objects.create(
                workflow=draft,
                key=spec["key"],
                name=spec.get("name", spec["key"].replace("_", " ").title()),
                step_class=spec.get("step_class", "fixture"),
                config=spec.get("config", {}),
                input_binding=spec.get("input_binding"),
                join_rule=spec.get("join_rule", workflow_models.JoinRule.ALL_SUCCESS),
                is_entry=index == 0 if "is_entry" not in spec else spec["is_entry"],
            )
        for source, target, condition in edges:
            Edge.objects.create(workflow=draft, source=by_key[source], target=by_key[target], condition=condition)
        return draft.publish()


def step_for(workflow: Workflow, key: str) -> Step:
    """Return one workflow step under elevated test read context."""

    with system_context(reason="test workflows step read"):
        return Step.objects.get(workflow=workflow, key=key)
