"""Previous-edge mutations retain the explicit writer and native M2M semantics."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.db import connection, connections, router, transaction
from django.db.models.signals import m2m_changed
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.workflows import engine
from angee.workflows.models import RunStatus, StepRunStatus
from tests.workflows import (
    Step,
    StepRun,
    WorkflowRun,
    WorkflowWriteRouter,
    reject_default_domain_query,
    workflow_actor,
    workflow_with_steps,
)
from tests.workflows import workflow_audit_frontier as workflow_audit_frontier
from tests.workflows import workflow_authorization_frontier as workflow_authorization_frontier


@pytest.fixture
def previous_writer(
    workflow_engine_tables: None,
    workflow_authorization_frontier: None,
    workflow_audit_frontier: list[dict[str, Any]],
    database_alias: Callable[[str], AbstractContextManager[str]],
) -> Iterator[str]:
    """Expose workflow tables through the shared database alias factory."""

    del workflow_engine_tables
    with database_alias("workflow_previous_writer") as alias:
        yield alias


@pytest.mark.django_db(transaction=True)
def test_previous_add_and_set_use_explicit_writer_with_native_signals(
    previous_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = workflow_with_steps(
        steps=tuple({"key": key} for key in ("a", "b", "c", "target")),
        edges=(("a", "b", "done"), ("b", "c", "done"), ("c", "target", "done")),
    )
    with system_context(reason="previous edge semantics setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        rows = {
            step.key: StepRun.objects.create(run=run, step=step)
            for step in Step.objects.filter(workflow=workflow)
        }
        a, b, c, target = (rows[key] for key in ("a", "b", "c", "target"))
        with transaction.atomic(using="default"):
            StepRun.objects.update_previous(target, [a, b], using="default")
            StepRun.objects.update_previous(a, [b], using="default")
        target = StepRun.objects.prefetch_related("previous").get(pk=target.pk)
        through = StepRun.previous.through
        retained_pk = through._base_manager.get(from_steprun_id=target.pk, to_steprun_id=b.pk).pk
    assert target._state.db == "default"
    observed: list[tuple[str, set[int]]] = []

    def observe(sender: Any, *, action: str, pk_set: set[int], **kwargs: Any) -> None:
        assert sender is through
        assert kwargs["instance"] is target
        assert kwargs["model"] is StepRun
        assert kwargs["reverse"] is False
        assert kwargs["using"] == previous_writer
        assert connections[previous_writer].in_atomic_block
        observed.append((action, set(pk_set)))

    routing = WorkflowWriteRouter("default")
    monkeypatch.setattr(router, "routers", [routing])
    # Odoo contributes a senderless receiver, so exercise that registration form.
    m2m_changed.connect(observe)
    try:
        with system_context(reason="previous edge semantics"), connection.execute_wrapper(reject_default_domain_query):
            with transaction.atomic(using=previous_writer):
                owner = StepRun.objects.db_manager("default")
                owner.update_previous(target, [b, c, c], using=previous_writer)
                owner.update_previous(target, [c], using=previous_writer)
                assert "previous" not in target._prefetched_objects_cache
                owner.update_previous(target, [b, c], replace=True, using=previous_writer)
                edges = through._base_manager.using(previous_writer).filter(from_steprun_id=target.pk)
                assert set(edges.values_list("to_steprun_id", flat=True)) == {b.pk, c.pk}
                assert edges.get(to_steprun_id=b.pk).pk == retained_pk
                owner.update_previous(target, [b, c], replace=True, using=previous_writer)
                owner.update_previous(target, [b, b, c], replace=True, using=previous_writer)
                owner.update_previous(target, [], replace=True, using=previous_writer)
                assert not edges.exists()
                assert through._base_manager.using(previous_writer).filter(
                    from_steprun_id=a.pk, to_steprun_id=b.pk
                ).exists()
    finally:
        m2m_changed.disconnect(observe)

    assert routing.writes == []
    assert observed == [
        ("pre_add", {c.pk}),
        ("post_add", {c.pk}),
        ("pre_add", set()),
        ("post_add", set()),
        ("pre_remove", {a.pk}),
        ("post_remove", {a.pk}),
        ("pre_add", set()),
        ("post_add", set()),
        ("pre_remove", {b.pk, c.pk}),
        ("post_remove", {b.pk, c.pk}),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["schedule", "skip", "map", "override"])
def test_engine_previous_writers_ignore_conflicting_write_router(
    previous_writer: str,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    del no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "entry"}, {"key": "target", "is_entry": False}),
        edges=(("entry", "target", "done"),),
    )
    actor = workflow_actor()
    with system_context(reason="previous writer callsite setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        source, target = (Step.objects.get(workflow=workflow, key=key) for key in ("entry", "target"))
        previous = StepRun.objects.create(run=run, step=source, status=StepRunStatus.SUCCEEDED, outcome="done")
    routing = WorkflowWriteRouter("default")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="previous writer callsite"), connection.execute_wrapper(reject_default_domain_query):
        with CaptureQueriesContext(connections[previous_writer]) as writer_sql:
            with transaction.atomic(using=previous_writer):
                if operation == "schedule":
                    engine._maybe_schedule_target(run, target, alias=previous_writer)
                elif operation == "skip":
                    engine._ensure_skipped(run, target, previous=[previous], alias=previous_writer)
                elif operation == "map":
                    engine._ensure_map_children(run, previous, target=target, items=[{}], alias=previous_writer)
                else:
                    previous = engine.override_run(run, [target], actor=actor, using=previous_writer)
                child = StepRun.objects.using(previous_writer).get(run_id=run.pk, step_id=target.pk)
                through = StepRun.previous.through
                assert through._base_manager.using(previous_writer).filter(
                    from_steprun_id=child.pk, to_steprun_id=previous.pk
                ).count() == 1

    assert any(
        query["sql"].startswith("INSERT") and through._meta.db_table in query["sql"] for query in writer_sql
    )


@pytest.mark.django_db(transaction=True)
def test_previous_edge_write_rolls_back_with_selected_transaction(
    previous_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    with system_context(reason="previous rollback setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        previous = StepRun.objects.create(run=run, system_kind="previous")
        target = StepRun.objects.create(run=run, system_kind="target")
    routing = WorkflowWriteRouter("default")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="previous rollback"), connection.execute_wrapper(reject_default_domain_query):
        with pytest.raises(RuntimeError, match="Abort previous edges"), transaction.atomic(using=previous_writer):
            StepRun.objects.update_previous(target, [previous], using=previous_writer)
            raise RuntimeError("Abort previous edges")
        assert not StepRun.previous.through._base_manager.using(previous_writer).filter(
            from_steprun_id=target.pk
        ).exists()

    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure_action", ["post_remove", "post_add"])
def test_previous_replacement_rolls_back_when_receiver_fails_without_outer_transaction(
    previous_writer: str, monkeypatch: pytest.MonkeyPatch, failure_action: str
) -> None:
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    with system_context(reason="previous receiver rollback setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        previous = StepRun.objects.create(run=run, system_kind="previous")
        replacement = StepRun.objects.create(run=run, system_kind="replacement")
        target = StepRun.objects.create(run=run, system_kind="target")
        StepRun.objects.update_previous(target, [previous], using="default")
        through = StepRun.previous.through
        original_pk = through._base_manager.get(from_steprun_id=target.pk).pk
    routing = WorkflowWriteRouter("default")
    monkeypatch.setattr(router, "routers", [routing])
    failures: list[str] = []

    def reject_change(sender: Any, *, action: str, using: str, **kwargs: Any) -> None:
        if action != failure_action:
            return
        assert sender is through
        assert kwargs["instance"] is target
        assert using == previous_writer
        assert connections[using].in_atomic_block
        previous_ids = set(
            through._base_manager.using(using)
            .filter(from_steprun_id=target.pk)
            .values_list("to_steprun_id", flat=True)
        )
        assert previous_ids == (set() if action == "post_remove" else {replacement.pk})
        failures.append(action)
        raise RuntimeError("Reject previous edge change")

    m2m_changed.connect(reject_change, sender=through)
    try:
        with (
            system_context(reason="previous receiver rollback"),
            connection.execute_wrapper(reject_default_domain_query),
        ):
            assert not connections[previous_writer].in_atomic_block
            with pytest.raises(RuntimeError, match="Reject previous edge change"):
                StepRun.objects.update_previous(target, [replacement], replace=True, using=previous_writer)
            assert not connections[previous_writer].in_atomic_block
            assert list(
                through._base_manager.using(previous_writer)
                .filter(from_steprun_id=target.pk)
                .values_list("pk", "to_steprun_id")
            ) == [(original_pk, previous.pk)]
    finally:
        m2m_changed.disconnect(reject_change, sender=through)

    assert failures == [failure_action]
    assert routing.writes == []
