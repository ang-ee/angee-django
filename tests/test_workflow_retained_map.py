"""Focused retained Map expansion, item-source and aggregation contracts."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import (
    AttemptCause,
    AttemptInput,
    AttemptResultKind,
    JsonPresence,
    MapItemSource,
)
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import HandlerStep, StepResult
from tests.workflows import (
    StepAttempt,
    StepRun,
    advance_once,
    execute_started,
    run_to_terminal,
    start_run,
    workflow_with_steps,
)

pytest_plugins = ("tests.workflows",)


def _map_workflow(*, item: Any, explicit: bool) -> Any:
    return workflow_with_steps(
        name="Retained Map item",
        steps=(
            {
                "key": "map",
                "step_class": "map",
                "config": {"target_step": "body", "items": [item]},
            },
            {
                "key": "body",
                "step_class": "handler",
                "input_binding": {"kind": "map_item", "path": []} if explicit else None,
            },
        ),
        edges=(),
    )


@pytest.mark.parametrize("item", [None, "scalar", {"item": "mapping"}])
@pytest.mark.django_db(transaction=True)
def test_explicit_map_item_uses_exact_raw_json_and_retained_source(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    item: Any,
) -> None:
    del workflow_engine_tables, no_workflow_queue

    def echo(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(step_run.input, outcome="done")

    monkeypatch.setattr(HandlerStep, "run", echo)
    run = start_run(_map_workflow(item=item, explicit=True))
    run_to_terminal(run)

    with system_context(reason="verify retained map item"):
        controller = StepRun.objects.get(run=run, step__key="map")
        body = StepRun.objects.get(run=run, step__key="body", map_index=0)
        controller_attempts = list(
            StepAttempt.objects.filter(step_run=controller).order_by("ordinal")
        )
        body_attempt = body.current_attempt
    assert run.status == RunStatus.SUCCEEDED
    assert body_attempt.output_present is True
    assert body_attempt.output == item
    assert body_attempt.map_expansion_id == controller_attempts[0].pk
    assert body_attempt.map_item_index == 0
    assert body_attempt.map_item_present is True
    assert body_attempt.map_item == item
    assert body_attempt.input_provenance == {
        "kind": "map_item",
        "expansion_attempt_id": controller_attempts[0].pk,
        "map_index": 0,
        "path": [],
    }
    assert [attempt.cause for attempt in controller_attempts] == [
        str(AttemptCause.MAP_ENGINE),
        str(AttemptCause.MAP_ENGINE),
    ]
    assert [attempt.result_kind for attempt in controller_attempts] == [
        str(AttemptResultKind.WAIT),
        str(AttemptResultKind.DONE),
    ]
    assert all(attempt.started_at is None for attempt in controller_attempts)
    assert run.steps_taken == 2


@pytest.mark.django_db(transaction=True)
def test_automatic_map_body_keeps_wrapped_input_and_captures_raw_source(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue

    def echo(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(step_run.input, outcome="done")

    monkeypatch.setattr(HandlerStep, "run", echo)
    run = start_run(_map_workflow(item="scalar", explicit=False))
    run_to_terminal(run)

    with system_context(reason="verify automatic map input"):
        body = StepRun.objects.get(run=run, step__key="body", map_index=0)
        attempt = body.current_attempt
    assert body.output == {"item": "scalar"}
    assert attempt.input == {"item": "scalar"}
    assert attempt.input_provenance == {"kind": "automatic"}
    assert attempt.map_item == "scalar"
    assert attempt.map_item_present is True
    assert body.current_map_expansion_id == attempt.map_expansion_id


@pytest.mark.django_db(transaction=True)
def test_map_capacity_failure_rolls_back_expansion_and_children(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    workflow = workflow_with_steps(
        name="Map capacity rollback",
        steps=(
            {
                "key": "map",
                "step_class": "map",
                "config": {"target_step": "body", "items": [1, 2, 3, 4, 5]},
            },
            {
                "key": "body",
                "step_class": "handler",
                "input_binding": {"kind": "map_item", "path": []},
            },
        ),
        edges=(),
    )
    run = start_run(workflow)
    with system_context(reason="inject dynamic Map capacity input"):
        run.steps_taken = 997
        run.save(update_fields=["steps_taken", "updated_at"])
        controller = StepRun.objects.get(run=run, step__key="map")

    advance_once(run)

    run.refresh_from_db()
    with system_context(reason="verify Map capacity rollback"):
        controller = StepRun.objects.get(run=run, step__key="map")
        assert StepAttempt.objects.filter(step_run=controller).count() == 0
        assert StepRun.objects.filter(run=run, step__key="body").count() == 0
    assert run.status == RunStatus.FAILED


@pytest.mark.django_db(transaction=True)
def test_map_reexpansion_reserves_reused_terminal_body_execution(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    workflow = _map_workflow(item="one", explicit=False)
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    with system_context(reason="prepare retained Map re-expansion capacity"):
        controller = StepRun.objects.get(run=run, step__key="map")
        before = StepAttempt.objects.filter(step_run=controller).count()
        models.QuerySet.update(type(workflow).objects.filter(pk=workflow.pk), max_steps=3)
    StepRun.objects.reschedule_for_override(controller.pk, input={}, at=timezone.now())

    assert advance_once(run) == []

    run.refresh_from_db()
    with system_context(reason="verify retained Map re-expansion capacity"):
        controller.refresh_from_db()
        assert StepAttempt.objects.filter(step_run=controller).count() == before
    assert run.status == RunStatus.FAILED
    assert run.steps_taken == 2
    assert controller.status == StepRunStatus.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_map_override_shrinks_current_membership_without_rebinding_history(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    workflow = workflow_with_steps(
        name="Map shrink",
        steps=(
            {"key": "map", "step_class": "map", "config": {"target_step": "body", "items": [1, 2, 3]}},
            {"key": "body", "step_class": "handler"},
        ),
        edges=(),
    )
    run = start_run(workflow)
    advance_once(run)
    with system_context(reason="load Map shrink generation"):
        controller = StepRun.objects.get(run=run, step__key="map")
        body_step = workflow.steps.get(key="body")
        old_expansion = controller.current_attempt
    now = timezone.now()
    controller = StepRun.objects.reschedule_for_override(controller.pk, input={}, at=now)
    with system_context(reason="configure next Map generation"):
        models.QuerySet.update(
            type(controller.step).objects.filter(pk=controller.step_id),
            config={"target_step": "body", "items": [1]},
        )
        controller.step.refresh_from_db()
    recorded = StepAttempt.objects.record_map_expansion(controller, at=now)
    assert recorded is not None
    new_expansion, _plan = recorded
    StepRun.objects.bind_map_membership(
        run_id=run.pk,
        target_id=body_step.pk,
        expansion_attempt_id=new_expansion.pk,
        item_count=1,
        at=now,
    )

    with system_context(reason="verify Map shrink membership"):
        children = list(
            StepRun.objects.filter(run=run, step=body_step).order_by("map_index")
        )
        old_attempts = list(StepAttempt.objects.filter(map_expansion=old_expansion))
    assert [child.current_map_expansion_id for child in children] == [
        new_expansion.pk,
        None,
        None,
    ]
    assert {attempt.map_expansion_id for attempt in old_attempts} == {old_expansion.pk}
    assert all(attempt.lease_revoked_at is not None for attempt in old_attempts)
    assert children[0].effect_generation == 1


@pytest.mark.django_db(transaction=True)
def test_map_aggregate_rejects_nonexpansion_attempt_without_projection(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    execute_started(run)
    with system_context(reason="load Map aggregate fence"):
        controller = StepRun.objects.get(run=run, step__key="map")
        body = StepRun.objects.get(run=run, step__key="body", map_index=0)
        before = StepAttempt.objects.filter(step_run=controller).count()

    with pytest.raises(ValidationError, match="current applied expansion"):
        StepAttempt.objects.record_map_aggregate(
            controller.pk,
            expansion_attempt_id=body.current_attempt_id,
            at=timezone.now(),
        )

    with system_context(reason="verify Map aggregate fence"):
        controller.refresh_from_db()
        assert StepAttempt.objects.filter(step_run=controller).count() == before
    assert controller.status == StepRunStatus.WAITING


@pytest.mark.django_db(transaction=True)
def test_map_membership_rejects_wrong_declared_target_without_rebinding(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    with system_context(reason="load Map target fence"):
        controller = StepRun.objects.get(run=run, step__key="map")
        body = StepRun.objects.get(run=run, step__key="body", map_index=0)

    with pytest.raises(ValidationError, match="expansion wait"):
        StepRun.objects.bind_map_membership(
            run_id=run.pk,
            target_id=controller.step_id,
            expansion_attempt_id=controller.current_attempt_id,
            item_count=1,
            at=timezone.now(),
        )

    with system_context(reason="verify Map target fence"):
        body.refresh_from_db()
    assert body.current_map_expansion_id == controller.current_attempt_id


@pytest.mark.django_db(transaction=True)
def test_map_aggregate_waits_for_complete_current_membership(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    with system_context(reason="load Map aggregate authority"):
        controller = StepRun.objects.get(run=run, step__key="map")
        before = StepAttempt.objects.filter(step_run=controller).count()

    assert StepAttempt.objects.record_map_aggregate(
        controller.pk,
        expansion_attempt_id=controller.current_attempt_id,
        at=timezone.now(),
    ) is None

    with system_context(reason="verify Map aggregate authority"):
        controller.refresh_from_db()
        assert StepAttempt.objects.filter(step_run=controller).count() == before
    assert controller.status == StepRunStatus.WAITING


@pytest.mark.django_db(transaction=True)
def test_map_expansion_owner_rejects_non_map_step_without_writes(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    workflow = workflow_with_steps(steps=({"key": "handler", "step_class": "handler"},), edges=())
    run = start_run(workflow)
    with system_context(reason="load non-Map expansion target"):
        handler = StepRun.objects.get(run=run, step__key="handler")

    with pytest.raises(ValidationError, match="requires a Map step"):
        StepAttempt.objects.record_map_expansion(handler, at=timezone.now())

    with system_context(reason="verify non-Map expansion rollback"):
        handler.refresh_from_db()
        assert StepAttempt.objects.filter(step_run=handler).count() == 0
    assert handler.status == StepRunStatus.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_invalid_map_definition_retains_failed_wait_and_aggregate(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    workflow = _map_workflow(item="one", explicit=False)
    with system_context(reason="inject persisted invalid Map definition"):
        map_step = workflow.steps.get(key="map")
        models.QuerySet.update(
            type(map_step).objects.filter(pk=map_step.pk),
            config={"target_step": "missing", "items": ["one"]},
        )
    run = start_run(workflow)
    run_to_terminal(run)

    with system_context(reason="verify invalid Map evidence"):
        controller = StepRun.objects.get(run=run, step__key="map")
        attempts = list(StepAttempt.objects.filter(step_run=controller).order_by("ordinal"))
    assert [attempt.result_kind for attempt in attempts] == [
        str(AttemptResultKind.WAIT),
        str(AttemptResultKind.DONE),
    ]
    assert attempts[1].outcome == "failed"
    assert attempts[1].output["error"]
    assert controller.status == StepRunStatus.SUCCEEDED
    assert controller.outcome == "failed"


@pytest.mark.django_db(transaction=True)
def test_retained_map_advance_ignores_system_journal_rows(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    with system_context(reason="add supported system journal row"):
        StepRun.objects.create(
            run=run,
            step=None,
            system_kind="join",
            status=StepRunStatus.SUCCEEDED,
        )

    run_to_terminal(run)

    assert run.status == RunStatus.SUCCEEDED
    with system_context(reason="verify retained Map with system journal"):
        assert StepRun.objects.filter(run=run, step__key="body", map_index=0).exists()


@pytest.mark.django_db(transaction=True)
def test_map_membership_identity_rejects_public_instance_and_bulk_initializers(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    with system_context(reason="load retained membership initializer guard"):
        controller = StepRun.objects.get(run=run, step__key="map")
        expansion = controller.current_attempt

    forged = StepRun(
        run=run,
        step=controller.step,
        map_index=99,
        current_map_expansion=expansion,
    )
    with pytest.raises(ValidationError, match="initialized by StepAttemptManager"):
        forged.save()
    with pytest.raises(TypeError, match="initialized by StepAttemptManager"):
        StepRun.objects.bulk_create([forged])

    with system_context(reason="verify retained membership initializer guard"):
        assert not StepRun.objects.filter(
            run=run,
            step=controller.step,
            map_index=99,
        ).exists()


@pytest.mark.django_db(transaction=True)
def test_map_claim_rejects_forged_sibling_step_membership(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    advance_once(run)
    with system_context(reason="forge sibling membership below public owner"):
        controller = StepRun.objects.get(run=run, step__key="map")
        expansion = controller.current_attempt
        forged = StepRun(
            run=run,
            step=controller.step,
            map_index=0,
            current_map_expansion=expansion,
        )
        models.QuerySet.bulk_create(StepRun.objects.all(), [forged])

    with pytest.raises(ValidationError, match="current applied expansion evidence"):
        StepAttempt.objects.claim(
            forged,
            input=AttemptInput(True, "one", {"kind": "map_item"}),
            map_item=MapItemSource(expansion.pk, 0, JsonPresence(True, "one")),
            claimed_at=timezone.now(),
        )

    with system_context(reason="verify forged sibling claim fence"):
        forged.refresh_from_db()
        assert not StepAttempt.objects.filter(step_run=forged).exists()
    assert forged.status == StepRunStatus.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_map_expansion_signal_cannot_forge_membership_inside_owner_session(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    monkeypatch.setattr(
        HandlerStep,
        "run",
        lambda self, step_run, *, now: StepResult.done(step_run.input, outcome="done"),
    )
    run = start_run(_map_workflow(item="one", explicit=False))
    with system_context(reason="load Map expansion signal guard"):
        controller = StepRun.objects.get(run=run, step__key="map")

    def forge_membership(sender: object, instance: StepAttempt, created: bool, **kwargs: Any) -> None:
        del sender, kwargs
        if created and instance.cause == str(AttemptCause.MAP_ENGINE):
            StepRun.objects.create(
                run_id=controller.run_id,
                step_id=controller.step_id,
                map_index=98,
                current_map_expansion=instance,
            )

    post_save.connect(forge_membership, sender=StepAttempt, weak=False)
    try:
        with pytest.raises(ValidationError, match="initialized by StepAttemptManager"):
            StepAttempt.objects.record_map_expansion(controller, at=timezone.now())
    finally:
        post_save.disconnect(forge_membership, sender=StepAttempt)

    with system_context(reason="verify Map expansion signal rollback"):
        controller.refresh_from_db()
        assert not StepAttempt.objects.filter(step_run=controller).exists()
        assert not StepRun.objects.filter(run=run, map_index=98).exists()
    assert controller.status == StepRunStatus.SCHEDULED
