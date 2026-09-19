"""Domain notifications cannot disappear across external-wait registration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import ArtifactSpec
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepExecutionMode, StepResult
from tests.workflows import (
    Step,
    StepAttempt,
    StepExternalSubscription,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
)

User = get_user_model()


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
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_subscription(monkeypatch)
    event = _confirm_and_retain_intent(user)
    assert event.kind == WorkflowDispatchKind.ARTIFACT_DELIVERY
    assert engine.deliver_artifact_dispatch(event.pk) == {"runs": 0, "woken": 0}

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 1
    with system_context(reason="verify event before subscription"):
        step_run.refresh_from_db()
        attempt.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED, (attempt.error, attempt.stacktrace)
    assert attempt.output == {"confirmed": True}
    assert engine.deliver_artifact_dispatch(event.pk) == {"runs": 0, "woken": 0}
    with system_context(reason="verify no resurrection"):
        step_run.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL subscription ordering")
def test_event_after_predicate_read_before_wait_commit_is_retained(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
                assert engine.deliver_artifact_dispatch(event.pk) == {"runs": 1, "woken": 0}
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
        assert engine.deliver_artifact_dispatch(event.pk) == {"runs": 0, "woken": 0}
    finally:
        release.set()
        _SubscribedPredicate.read_done = None
        _SubscribedPredicate.release = None
