"""Workflow previous transactions."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connections, transaction
from django.db.models.signals import m2m_changed
from rebac import system_context

from angee.workflows.models import RunStatus
from tests.workflows import Step, StepRun, WorkflowRun, workflow_with_steps


@pytest.mark.django_db(transaction=True)
def test_previous_add_and_set_preserve_native_signals(
    workflow_engine_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = workflow_with_steps(
        steps=tuple(({"key": key} for key in ("a", "b", "c", "target"))),
        edges=(("a", "b", "done"), ("b", "c", "done"), ("c", "target", "done")),
    )
    with system_context(reason="previous edge semantics setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        rows = {step.key: StepRun.objects.create(run=run, step=step) for step in Step.objects.filter(workflow=workflow)}
        a, b, c, target = (rows[key] for key in ("a", "b", "c", "target"))
        with transaction.atomic():
            StepRun.objects.update_previous(target, [a, b])
            StepRun.objects.update_previous(a, [b])
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
        assert kwargs["using"] == "default"
        assert connections["default"].in_atomic_block
        observed.append((action, set(pk_set)))

    m2m_changed.connect(observe)
    try:
        with system_context(reason="previous edge semantics"):
            with transaction.atomic():
                owner = StepRun.objects
                owner.update_previous(target, [b, c, c])
                owner.update_previous(target, [c])
                assert "previous" not in target._prefetched_objects_cache
                owner.update_previous(target, [b, c], replace=True)
                edges = through._base_manager.filter(from_steprun_id=target.pk)
                assert set(edges.values_list("to_steprun_id", flat=True)) == {b.pk, c.pk}
                assert edges.get(to_steprun_id=b.pk).pk == retained_pk
                owner.update_previous(target, [b, c], replace=True)
                owner.update_previous(target, [b, b, c], replace=True)
                owner.update_previous(target, [], replace=True)
                assert not edges.exists()
                assert through._base_manager.filter(from_steprun_id=a.pk, to_steprun_id=b.pk).exists()
    finally:
        m2m_changed.disconnect(observe)
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
def test_previous_edge_write_rolls_back_with_transaction(
    workflow_engine_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    with system_context(reason="previous rollback setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        previous = StepRun.objects.create(run=run, system_kind="previous")
        target = StepRun.objects.create(run=run, system_kind="target")
    with system_context(reason="previous rollback"):
        with pytest.raises(RuntimeError, match="Abort previous edges"), transaction.atomic():
            StepRun.objects.update_previous(target, [previous])
            raise RuntimeError("Abort previous edges")
        assert not StepRun.previous.through._base_manager.filter(from_steprun_id=target.pk).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure_action", ["post_remove", "post_add"])
def test_previous_replacement_rolls_back_when_receiver_fails_without_outer_transaction(
    workflow_engine_tables: None, monkeypatch: pytest.MonkeyPatch, failure_action: str
) -> None:
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    with system_context(reason="previous receiver rollback setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        previous = StepRun.objects.create(run=run, system_kind="previous")
        replacement = StepRun.objects.create(run=run, system_kind="replacement")
        target = StepRun.objects.create(run=run, system_kind="target")
        StepRun.objects.update_previous(target, [previous])
        through = StepRun.previous.through
        original_pk = through._base_manager.get(from_steprun_id=target.pk).pk
    failures: list[str] = []

    def reject_change(sender: Any, *, action: str, using: str, **kwargs: Any) -> None:
        if action != failure_action:
            return
        assert sender is through
        assert kwargs["instance"] is target
        assert using == "default"
        assert connections[using].in_atomic_block
        previous_ids = set(
            through._base_manager.filter(from_steprun_id=target.pk).values_list("to_steprun_id", flat=True)
        )
        assert previous_ids == (set() if action == "post_remove" else {replacement.pk})
        failures.append(action)
        raise RuntimeError("Reject previous edge change")

    m2m_changed.connect(reject_change, sender=through)
    try:
        with system_context(reason="previous receiver rollback"):
            assert not connections["default"].in_atomic_block
            with pytest.raises(RuntimeError, match="Reject previous edge change"):
                StepRun.objects.update_previous(target, [replacement], replace=True)
            assert not connections["default"].in_atomic_block
            assert list(through._base_manager.filter(from_steprun_id=target.pk).values_list("pk", "to_steprun_id")) == [
                (original_pk, previous.pk)
            ]
    finally:
        m2m_changed.disconnect(reject_change, sender=through)
    assert failures == [failure_action]
