"""The public harness composes registration, admission and node execution."""

import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db.models.signals import post_save
from django.test import override_settings
from rebac import actor_context, system_context
from rebac.models import active_relationship_model

from angee.base.scoping import system_queryset
from angee.graphql.publishing import change_published
from angee.jobs.enqueue import celery_app
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from angee.workflows.states import StepRunStatus
from angee.workflows.steps import Step, resolve_step
from angee.workflows.testing import drivers
from angee.workflows.testing.models import StepRun, Workflow, WorkflowVersion
from tests.conftest import Vault, make_addon
from tests.workflow_steps import Value, document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


def test_capture_tasks_fixture_and_scoped_failure(capture_tasks):
    """The fixture and context manager restore the previous sender after failure."""
    celery_app.send_task("outer", kwargs={"id": 1})
    with drivers.capture_tasks(error=RuntimeError("send failed")) as nested:
        with pytest.raises(RuntimeError, match="send failed"):
            celery_app.send_task("inner", kwargs={"id": 2})
    celery_app.send_task("outer-again", kwargs={"id": 3})
    assert [name for name, _ in capture_tasks] == ["outer", "outer-again"]
    assert [name for name, _ in nested] == ["inner"]


def test_observe_scopes_model_and_disconnects_after_exit():
    """Observation only records the chosen sender while the context is active."""
    payload = object()
    with drivers.observe(Workflow) as published:
        change_published.send(sender=WorkflowVersion, payload=object())
        change_published.send(sender=Workflow, payload=payload)
    change_published.send(sender=Workflow, payload=object())
    assert published == [payload]


def test_trigger_source_restores_opt_in_and_native_receiver():
    """The mixin declares opt-in; the driver scopes its native receiver."""
    assert issubclass(Vault, drivers.RecordChangedOptIn)
    with drivers.trigger_source(Vault, connect=True):
        receivers = post_save.send(sender=Vault, instance=object(), raw=True, created=False)
        assert any(receiver == drivers.RecordChanged.changed for receiver, _ in receivers)
    receivers = post_save.send(sender=Vault, instance=object(), raw=True, created=False)
    assert not any(receiver == drivers.RecordChanged.changed for receiver, _ in receivers)


@pytest.fixture
def workflow_resource(tmp_path, monkeypatch):
    """Register a real resource manifest in the test's temporary app registry."""
    try:
        with monkeypatch.context() as patch:
            def declare(rows, *, name="tests.workflow_resource", tier="install", grants=(), extra_entries=()):
                directory = tmp_path / name
                directory.mkdir()
                source = "100_workflows.workflow.yaml"
                (directory / source).write_text(yaml.safe_dump({"rows": rows}))
                entries = [{"path": source}, *extra_entries]
                if grants:
                    (directory / "200_grants.yaml").write_text(yaml.safe_dump(list(grants)))
                    entries.append({"path": "200_grants.yaml", "kind": "grants"})
                addon = make_addon(name=name, path=directory, resources={tier: entries})
                addon.apps = apps
                addon.models = {}
                patch.setitem(apps.app_configs, addon.label, addon)
                apps.clear_cache()
                return addon

            yield declare
    finally:
        apps.clear_cache()


def workflow_row(xref="graph", **fields):
    """One native resource row; callers vary only the behavior under proof."""
    return {
        "xref": xref,
        "fields": {"key": "resource-graph", "name": "Resource graph", "draft": document("entry"), **fields},
    }


def test_registration_restores_nested_settings_and_local_classes(settings):
    """Temporary registrations restore both owners, including exceptional exits."""

    class TemporaryStep(Step[None, None, None]):
        """A function-local implementation contributed only during its context."""

        key = "temporary_step"

    class ReplacementStep(TemporaryStep):
        """A nested override of the same declared key."""

    registry = settings.ANGEE_WORKFLOW_STEP_CLASSES
    module = sys.modules[__name__]
    assert not hasattr(module, "TemporaryStep")
    assert not hasattr(module, "ReplacementStep")
    with pytest.raises(RuntimeError, match="body failure"):
        with drivers.register_steps(TemporaryStep):
            assert resolve_step(TemporaryStep.key) is TemporaryStep
            with drivers.register_steps(ReplacementStep):
                assert resolve_step(TemporaryStep.key) is ReplacementStep
            assert resolve_step(TemporaryStep.key) is TemporaryStep
            assert not hasattr(module, "ReplacementStep")
            raise RuntimeError("body failure")
    assert settings.ANGEE_WORKFLOW_STEP_CLASSES is registry
    assert not hasattr(module, "TemporaryStep")
    assert not hasattr(module, "ReplacementStep")


@pytest.mark.parametrize("registration_first", [True, False])
def test_registration_does_not_restore_unrelated_settings(settings, registration_first):
    """Independent fixture stacks may close without nesting their settings changes."""

    class TemporaryStep(Step[None, None, None]):
        """A registration must not capture unrelated worker configuration."""

        key = "temporary_independent_fixture"

    with override_settings(CELERY_TASK_SOFT_TIME_LIMIT=840, CELERY_TASK_TIME_LIMIT=900):
        registry = settings.ANGEE_WORKFLOW_STEP_CLASSES
        with ExitStack() as cleanup:
            worker_settings, registrations = ExitStack(), ExitStack()
            cleanup.callback(worker_settings.close)
            cleanup.callback(registrations.close)
            worker_settings.enter_context(override_settings(
                CELERY_TASK_SOFT_TIME_LIMIT=120, CELERY_TASK_TIME_LIMIT=180,
            ))
            registrations.enter_context(drivers.register_steps(TemporaryStep))
            assert resolve_step(TemporaryStep.key) is TemporaryStep

            first, last = (registrations, worker_settings) if registration_first else (worker_settings, registrations)
            first.close()
            last.close()

            assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 840
            assert settings.CELERY_TASK_TIME_LIMIT == 900
            assert settings.ANGEE_WORKFLOW_STEP_CLASSES is registry
            assert TemporaryStep.key not in registry
            assert not hasattr(sys.modules[__name__], "TemporaryStep")


def test_registered_local_step_runs_through_the_production_driver(execution, register_step):
    """A function-local step participates in normal definition validation and execution."""

    class Increment(Step[Value, Value, None]):
        """Increment one value to prove the registered body was executed."""

        key = "increment"

        def run(self, ctx: Any):
            """Return the declared output through the normal settlement contract."""
            return ctx.done({"value": ctx.input.value + 1})

    register_step(Increment)
    actor, sent = execution
    workflow = drivers.load_workflow(document("entry", step="increment"), actor=actor)
    run = drivers.start_run(workflow, actor=actor, input={"value": 4})

    drivers.run_until(run)

    assert run.output == {"value": 5}
    assert [name for name, _payload in sent] == ["workflows.execute", "workflows.wake_run"]


@pytest.mark.parametrize("status", [StepRunStatus.READY, StepRunStatus.SUCCEEDED])
def test_run_factory_reaches_node_with_real_predecessor_output(execution, run_factory, status):
    """Factory placement executes predecessors and honors the requested target status."""

    actor, _sent = execution
    workflow = drivers.load_workflow(document("first", "second"), actor=actor)

    run = run_factory(workflow).at("second", status=status, input={"value": 7})

    step_runs = {step_run.node_key: step_run for step_run in system_queryset(StepRun).filter(run=run)}
    assert step_runs["first"].status == StepRunStatus.SUCCEEDED
    assert step_runs["first"].output == {"value": 7}
    assert step_runs["second"].status == status
    if status == StepRunStatus.SUCCEEDED:
        assert step_runs["second"].input == {"value": 7}
        assert run.output == {"value": 7}


def test_run_factory_reaches_a_real_wait(execution, run_factory):
    """A requested waiting status preserves the target's actual checkpoint."""

    actor, _sent = execution
    workflow = drivers.load_workflow(document("entry", step="pause"), actor=actor)

    run = run_factory(workflow).at("entry", status=StepRunStatus.WAITING)

    step_run = system_queryset(StepRun).get(run=run)
    assert step_run.state == {"resumed": True}
    assert step_run.attempts.with_actor(actor).count() == 1


def test_resource_key_names_the_addon_and_resource():
    """Malformed resource selectors fail before touching persistence."""

    with pytest.raises(ValueError, match="unresolved xref"):
        drivers.load_workflow("unqualified")


@pytest.mark.parametrize(
    "publish,expected", [(None, True), (True, True), (False, False), ("0", False), ("false", False)],
)
def test_xref_driver_uses_native_publish_widget_defaults_and_retains_ledger(
    execution, workflow_resource, publish, expected,
):
    """Xref loading keeps native row cleaning and idempotent publication identity."""
    actor, _sent = execution
    fields = {} if publish is None else {"publish": publish}
    addon = workflow_resource([workflow_row(**fields)])
    handle = f"{addon.name}.graph"

    workflow = drivers.load_workflow(handle, actor=actor)
    replay = drivers.load_workflow(handle, actor=actor)

    assert workflow.pk == replay.pk
    assert bool(workflow.published_id) is expected
    assert replay.published_id == workflow.published_id
    assert Resource.objects.count() == 1
    assert Resource.objects.get().source_addon == addon.name
    with actor_context(actor):
        assert Resource.objects.get().target_instance() == workflow
        if expected:
            assert workflow.published.published_by_id == actor.pk


def test_xref_driver_selects_longest_addon_prefix_and_only_requested_row(execution, workflow_resource):
    """A dotted xref selects one row without importing sibling rows or grants."""
    actor, _sent = execution
    workflow_resource([workflow_row("child.graph.detail", key="wrong-parent")], name="tests.resource_parent")
    addon = workflow_resource(
        [workflow_row("graph.detail"), workflow_row("other", key="unselected", draft={"invalid": "document"})],
        name="tests.resource_parent.child",
        grants=[{"resource": "storage/drive:unselected-resource", "relation": "editor", "subject": "auth/user:*"}],
    )
    workflow = drivers.load_workflow(f"{addon.name}.graph.detail", actor=actor)

    assert workflow.key == "resource-graph"
    assert list(system_queryset(Workflow).values_list("key", flat=True)) == ["resource-graph"]
    assert list(Resource.objects.values_list("xref", flat=True)) == ["graph.detail"]
    with system_context(reason="assert unrelated resource grants remain unimported"):
        assert not active_relationship_model()._default_manager.filter(
            resource_type="storage/drive", resource_id="unselected-resource", relation="editor",
            subject_type="auth/user", subject_id="*",
        ).exists()


def test_xref_driver_preserves_ambient_actor_on_installation(execution, workflow_resource):
    """Loader elevation must not replace the caller's publication attribution."""
    actor, _sent = execution
    addon = workflow_resource([workflow_row()])
    with actor_context(actor):
        workflow = drivers.load_workflow(f"{addon.name}.graph")
    assert workflow.published.published_by_id == actor.pk


def test_xref_lookup_does_not_order_unrelated_external_dependencies(execution, workflow_resource):
    """A selected row does not need its sibling's external dependency graph."""
    actor, _sent = execution
    sibling = "200_workflows.workflow.yaml"
    addon = workflow_resource(
        [workflow_row()],
        extra_entries=[{"path": sibling, "depends_on": ["external.addon:resources/prerequisite.yaml"]}],
    )
    (Path(addon.path) / sibling).write_text(yaml.safe_dump({"rows": [workflow_row("other", key="unselected")]}))

    workflow = drivers.load_workflow(f"{addon.name}.graph", actor=actor)

    assert workflow.key == "resource-graph"
    assert list(system_queryset(Workflow).values_list("key", flat=True)) == ["resource-graph"]
    assert list(Resource.objects.values_list("xref", flat=True)) == ["graph"]


@pytest.mark.parametrize("ambient", [False, True])
def test_xref_driver_denies_unauthorized_unchanged_replay(execution, workflow_resource, ambient):
    """Native hash skips still require the caller's write permission."""
    actor, _sent = execution
    addon = workflow_resource([workflow_row()])
    handle = f"{addon.name}.graph"
    workflow = drivers.load_workflow(handle, actor=actor)
    with system_context(reason="create unprivileged workflow resource caller"):
        other = get_user_model().objects.create_user(username="resource-non-writer")
    if ambient:
        with actor_context(other), pytest.raises(PermissionDenied):
            drivers.load_workflow(handle)
    else:
        with pytest.raises(PermissionDenied):
            drivers.load_workflow(handle, actor=other)
    assert Resource.objects.count() == 1
    assert system_queryset(WorkflowVersion).filter(workflow=workflow).count() == 1


def test_xref_loading_keeps_native_demo_tier_guard(execution, workflow_resource, settings):
    actor, _sent = execution
    addon = workflow_resource([workflow_row()], tier="demo")
    handle = f"{addon.name}.graph"
    settings.DEBUG = False
    with pytest.raises(ImproperlyConfigured, match="requires DEBUG"):
        drivers.load_workflow(handle, actor=actor)
    assert not Resource.objects.exists()
    workflow = drivers.load_workflow(handle, actor=actor, allow_non_dev=True)
    assert settings.DEBUG is False
    assert drivers.load_workflow(handle, actor=actor, allow_non_dev=True).pk == workflow.pk
    settings.DEBUG = True
    assert drivers.load_workflow(handle, actor=actor).pk == workflow.pk


def test_xref_loading_rejects_missing_and_wrong_model_targets(execution, workflow_resource):
    actor, _sent = execution
    addon = workflow_resource([workflow_row()])
    with pytest.raises(LookupError, match="not found"):
        drivers.load_workflow(f"{addon.name}.missing", actor=actor)
    with pytest.raises(ResourceLoadError, match="expected workflows.WorkflowVersion"):
        Resource.objects.load_xref(f"{addon.name}.graph", model=WorkflowVersion, actor=actor)
    assert not Resource.objects.exists()
    assert not system_queryset(Workflow).exists()


def test_xref_loading_rejects_declaration_collisions(execution, workflow_resource):
    actor, _sent = execution
    addon = workflow_resource([workflow_row(), workflow_row(key="collision")])
    with pytest.raises(ResourceLoadError, match="xref collision"):
        drivers.load_workflow(f"{addon.name}.graph", actor=actor)
    assert not Resource.objects.exists()
