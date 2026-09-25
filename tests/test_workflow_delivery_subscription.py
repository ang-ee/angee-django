"""Domain notifications cannot disappear across external-wait registration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.base.identity import public_id_for
from angee.testing.models import (
    Edge,
    Step,
    StepAttempt,
    StepExternalSubscription,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
)
from angee.workflows import engine
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptResult,
    AttemptResultKind,
    JsonPresence,
    RecoveryMode,
)
from angee.workflows.data_contracts import schema_data_contract
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunOrigin, RunStatus, StepRunStatus
from angee.workflows.steps import (
    JoinContinuation,
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
)
from tests.workflows import advance_once, execute_started, run_to_terminal, start_run

User = get_user_model()


@pytest.mark.parametrize(
    ("child_id_path", "step_input"),
    [
        (["continuation_id"], {"continuation_id": "child"}),
        (["children", "0", "id"], {"children": [{"id": "child"}]}),
    ],
)
def test_continuation_join_uses_exponential_bounded_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    child_id_path: list[str],
    step_input: dict[str, Any],
) -> None:
    publication = SimpleNamespace(
        output_schema={"type": "object"},
        subject_declaration="",
        result_rules=[{"outcome": "completed"}],
    )
    child = SimpleNamespace(workflow=publication)
    joined_child_ids: list[str] = []

    def join_continuation(*args: Any, **kwargs: Any) -> tuple[SimpleNamespace, None]:
        joined_child_ids.append(kwargs["child_id"])
        return child, None

    monkeypatch.setattr(type(StepAttempt.objects), "join_continuation", join_continuation)
    step_run = SimpleNamespace(
        pk=1,
        _state=SimpleNamespace(adding=False, db="default"),
        current_attempt=SimpleNamespace(lease_token="join-lease"),
        input=step_input,
        run=SimpleNamespace(execution_admission_actor=lambda: object()),
        resume_state={},
        step=SimpleNamespace(
            config={
                "child_id_path": child_id_path,
                "expected_starter_class": "start_continuation",
                "expected_output_schema": {"type": "object"},
                "expected_subject": "",
                "expected_outcomes": ["completed"],
            }
        ),
    )
    now = timezone.now()

    for expected in (900, 1800, 3600, 3600):
        result = JoinContinuation().run(step_run, now=now)
        assert result.kind == "wait"
        assert result.waiting_kind == "external"
        assert result.until == now + timedelta(seconds=expected)
        assert result.resume_state == {"join_reconcile_after": expected}
        step_run.resume_state = result.resume_state
    assert joined_child_ids == ["child"] * 4


def test_terminal_failed_continuation_routes_child_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    publication = SimpleNamespace(
        output_schema={"type": "object"},
        subject_declaration="",
        result_rules=[{"outcome": "completed"}],
    )
    child = SimpleNamespace(workflow=publication)
    completion = SimpleNamespace(status=RunStatus.FAILED, result={"status": "failed"})
    monkeypatch.setattr(type(StepAttempt.objects), "join_continuation", lambda *args, **kwargs: (child, completion))
    step_run = SimpleNamespace(
        pk=1,
        _state=SimpleNamespace(adding=False, db="default"),
        current_attempt=SimpleNamespace(lease_token="join-lease"),
        input={"continuation_id": "child"},
        run=SimpleNamespace(execution_admission_actor=lambda: object()),
        step=SimpleNamespace(
            config={
                "child_id_path": ["continuation_id"],
                "expected_starter_class": "start_continuation",
                "expected_output_schema": {"type": "object"},
                "expected_subject": "",
                "expected_outcomes": ["completed"],
            }
        ),
    )

    result = JoinContinuation().run(step_run, now=timezone.now())

    assert result.kind == "done"
    assert result.outcome == "child_failed"
    assert result.output_present is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL continuation delivery ordering")
def test_continuation_delivery_between_completion_read_and_wait_commit_is_retained(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    actor = User.objects.create_user(username="continuation-delivery-owner")
    with system_context(reason="continuation delivery child definition"):
        child_head = Workflow.objects.create(name="Continuation delivery child", created_by=actor)
        Step.objects.create(
            workflow=child_head,
            key="finish",
            name="Finish",
            step_class="fixture",
            is_entry=True,
        )
        child_publication = child_head.publish()

    class StartContinuation(StepImpl):
        effect = StepEffect.WRITE
        execution_mode = StepExecutionMode.DATABASE_COMMAND
        replay_mode = RecoveryMode.FRESH
        outcomes = (StepOutcome("started", "Started"),)

        @classmethod
        def declared_output_contract(cls, config: Any) -> Any:
            del config
            return schema_data_contract(
                {
                    "type": "object",
                    "required": ["continuation_id"],
                    "properties": {"continuation_id": {"type": "string"}},
                    "additionalProperties": False,
                }
            )

        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del self, now
            child = engine.start(
                child_publication,
                subject=None,
                actor=step_run.run.execution_admission_actor(),
                parent_step_run=step_run,
                parent_relation="continuation",
                origin=RunOrigin.WORKFLOW,
                input=JsonPresence(True, {}),
            )
            return StepResult.done(
                {"continuation_id": public_id_for(WorkflowRun, child.pk)},
                outcome="started",
            )

    with system_context(reason="continuation delivery parent definition"):
        parent_head = Workflow.objects.create(name="Continuation delivery parent", created_by=actor)
        handoff = Step.objects.create(
            workflow=parent_head,
            key="handoff",
            name="Handoff",
            step_class="fixture",
            is_entry=True,
        )
        join = Step.objects.create(
            workflow=parent_head,
            key="join",
            name="Join",
            step_class="join_continuation",
            input_binding={"kind": "step_output", "step_key": "handoff", "path": []},
            config={
                "child_id_path": ["continuation_id"],
                "expected_starter_class": "fixture",
                "expected_output_schema": child_publication.output_schema,
                "expected_subject": "",
                "expected_outcomes": ["completed"],
            },
        )
        Edge.objects.create(
            workflow=parent_head,
            source=handoff,
            target=join,
            condition="started",
        )
        original_resolve = type(handoff).resolve_impl
        monkeypatch.setattr(
            type(handoff),
            "resolve_impl",
            lambda self, field: StartContinuation if self.key == "handoff" else original_resolve(self, field),
        )
        parent_publication = parent_head.publish()

    parent = start_run(parent_publication, actor=actor)
    assert [row.step.key for row in advance_once(parent)] == ["handoff"]
    execute_started(parent)
    with system_context(reason="continuation delivery child inspection"):
        child = WorkflowRun.objects.get(parent_step_run__run=parent, parent_relation="continuation")
    assert [row.step.key for row in advance_once(parent)] == ["join"]
    with system_context(reason="continuation delivery join dispatch"):
        join_row = StepRun.objects.get(run=parent, step__key="join")
        join_attempt = join_row.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=join_attempt)

    completion_read, release = Event(), Event()
    manager_class = type(StepAttempt.objects)
    original_completion = manager_class.join_continuation

    def completion_barrier(manager: Any, *args: Any, **kwargs: Any) -> tuple[Any, Any | None]:
        result = original_completion(manager, *args, **kwargs)
        completion_read.set()
        if not release.wait(timeout=5):
            raise RuntimeError("Continuation completion barrier timed out.")
        return result

    monkeypatch.setattr(manager_class, "join_continuation", completion_barrier)

    def invoke_join() -> dict[str, int]:
        close_old_connections()
        try:
            with system_context(reason="continuation join worker"):
                return engine.execute_dispatch(
                    dispatch.pk,
                    join_attempt.pk,
                    join_attempt.lease_token,
                )
        finally:
            connections.close_all()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            executing = pool.submit(invoke_join)
            try:
                assert completion_read.wait(timeout=5)
                run_to_terminal(child)
                with system_context(reason="continuation terminal delivery inspection"):
                    delivery = WorkflowDispatch.objects.get(
                        kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
                        artifact_object_id=child.pk,
                    )
                assert WorkflowDispatch.objects.deliver(
                    delivery.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY
                ) == {"runs": 1, "woken": 0}
            finally:
                release.set()
            assert executing.result(timeout=10)["executed"] == 1
        with system_context(reason="continuation wait retention inspection"):
            join_row.refresh_from_db()
            join_attempt.refresh_from_db()
            subscription = StepExternalSubscription.objects.get(attempt=join_attempt)
            queryset = StepExternalSubscription.objects.filter(pk=subscription.pk)
            with pytest.raises(TypeError, match="retained for their attempt lifecycle"):
                queryset._raw_delete(using=queryset.db)
        assert join_row.status == StepRunStatus.WAITING
        assert join_row.wait_until is not None and join_row.wait_until <= join_attempt.result_recorded_at
        assert subscription.target == child
    finally:
        release.set()


@pytest.mark.django_db(transaction=True)
def test_recovery_terminal_delivery_targets_the_lineage_root(
    composed_tables: None,
    no_workflow_queue: None,
) -> None:
    del composed_tables, no_workflow_queue
    actor = User.objects.create_user(username="recovery-delivery-owner")
    with system_context(reason="recovery delivery fixture"):
        workflow = Workflow.objects.create(name="Recovery delivery", created_by=actor)
        step = Step.objects.create(
            workflow=workflow,
            key="operation",
            name="Operation",
            step_class="fixture",
            is_entry=True,
        )
        parent = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        parent_step = StepRun.objects.create(
            run=parent,
            step=step,
            status=StepRunStatus.SUCCEEDED,
        )
        original = WorkflowRun.objects.create(
            workflow=workflow,
            origin=RunOrigin.WORKFLOW,
            status=RunStatus.RUNNING,
            parent_step_run=parent_step,
            parent_relation="continuation",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        failed_step = StepRun.objects.create(
            run=original,
            step=step,
            status=StepRunStatus.SCHEDULED,
        )
    failed_attempt = StepAttempt.objects.claim(failed_step, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        failed_attempt.pk,
        lease_token=failed_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        failed_attempt.pk,
        lease_token=failed_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="retained child failure"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="recovery delivery terminal transitions"):
        original.refresh_from_db()
        original.mark_failed("Retained child failed.")
        recovery = WorkflowRun.objects.create(
            workflow=workflow,
            origin=RunOrigin.RECOVERY,
            status=RunStatus.RUNNING,
            recovery_source_attempt=failed_attempt,
            recovery_request_actor_ref=str(to_subject_ref(actor)),
            recovery_mode=RecoveryMode.FRESH,
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        recovery.mark_succeeded()
        deliveries = list(
            WorkflowDispatch.objects.filter(
                kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            ).order_by("pk")
        )

    assert len(deliveries) == 2
    assert {delivery.artifact_object_id for delivery in deliveries} == {original.pk}


class _SubscribedPredicate:
    execution_mode = StepExecutionMode.STANDARD
    read_done: Event | None = None
    release: Event | None = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        with system_context(reason="fixture subscribed domain read"):
            user = User.objects.get(pk=step_run.input["user_id"])
        engine.subscribe_external(step_run, (user,))
        with system_context(reason="fixture predicate after subscription"):
            user.refresh_from_db()  # The predicate is read after subscription commits.
        if self.read_done is not None:
            self.read_done.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise RuntimeError("Subscription test release timed out.")
        if user.first_name == "confirmed":
            return StepResult.done(output={"confirmed": True}, outcome="confirmed")
        return StepResult.wait(
            until=now + timedelta(minutes=1),
            waiting_kind="external",
            artifacts=(ArtifactSpec(user, "Pending domain confirmation"),),
        )


def _scheduled_subscription(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    user = User.objects.create_user(username=f"subscription-{timezone.now().timestamp()}", first_name="pending")
    with system_context(reason="subscription workflow fixture"):
        workflow = Workflow.objects.create(name="Subscribed predicate", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="confirm",
            name="Confirm",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(
            run=run,
            step=step,
            status=StepRunStatus.SCHEDULED,
            input={"user_id": user.pk},
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _SubscribedPredicate)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(pulse.pk)["claimed"] == 1
    with system_context(reason="subscription fixture inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    return user, step_run, attempt, dispatch


def _confirm_and_retain_intent(user: Any) -> Any:
    with transaction.atomic(), system_context(reason="domain confirmation transaction"):
        User.objects.filter(pk=user.pk).update(first_name="confirmed")
        return engine.schedule_artifact_delivery(user)


@pytest.mark.django_db(transaction=True)
def test_event_before_subscription_is_read_as_current_domain_state(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_subscription(monkeypatch)
    event = _confirm_and_retain_intent(user)
    assert event.kind == WorkflowDispatchKind.ARTIFACT_DELIVERY
    assert WorkflowDispatch.objects.deliver(
        event.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, now=timezone.now(),
    ) == {"runs": 0, "woken": 0}

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 1
    with system_context(reason="verify event before subscription"):
        step_run.refresh_from_db()
        attempt.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED, (attempt.error, attempt.stacktrace)
    assert attempt.output == {"confirmed": True}
    assert WorkflowDispatch.objects.deliver(
        event.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
    ) == {"runs": 0, "woken": 0}
    with system_context(reason="verify no resurrection"):
        step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL subscription ordering")
def test_event_after_predicate_read_before_wait_commit_is_retained(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_subscription(monkeypatch)
    read_done, release = Event(), Event()
    _SubscribedPredicate.read_done = read_done
    _SubscribedPredicate.release = release

    def invoke() -> dict[str, int]:
        close_old_connections()
        try:
            with system_context(reason="subscribed predicate worker"):
                return engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)
        finally:
            connections.close_all()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            executing = pool.submit(invoke)
            try:
                assert read_done.wait(timeout=5)
                event = _confirm_and_retain_intent(user)
                assert WorkflowDispatch.objects.deliver(
                    event.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY
                ) == {"runs": 1, "woken": 0}
            finally:
                release.set()
            assert executing.result(timeout=10)["executed"] == 1
        with system_context(reason="verify event during wait registration"):
            step_run.refresh_from_db()
            attempt.refresh_from_db()
            run = WorkflowRun.objects.get(pk=step_run.run_id)
            subscription = StepExternalSubscription.objects.get(attempt=attempt)
        assert run.deliveries == 1
        assert step_run.status == StepRunStatus.WAITING
        assert step_run.wait_until is not None and step_run.wait_until <= attempt.result_recorded_at
        assert subscription.target == user
        assert attempt.external_object_id is None
        assert WorkflowDispatch.objects.deliver(event.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY) == {
            "runs": 0,
            "woken": 0,
        }
    finally:
        release.set()
        _SubscribedPredicate.read_done = None
        _SubscribedPredicate.release = None
