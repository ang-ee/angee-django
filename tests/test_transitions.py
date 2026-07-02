"""Tests for guarded ``StateField`` transitions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connection, models

from angee.base.fields import StateField
from angee.base.transitions import ANY, StateTransitions, TransitionNotAllowed, transition


def is_ready(instance: Any) -> bool:
    """Return whether the row can leave draft."""

    return bool(instance.ready)


def actor_is_owner(instance: Any, actor: Any) -> bool:
    """Return whether ``actor`` owns the transition action."""

    return actor == instance.owner


def remember_success(instance: Any, source: Any, target: Any) -> None:
    """Record transition hook arguments on the instance."""

    instance.success_events.append((instance, source, target))


class TransitionTask(models.Model):
    """Concrete throwaway model used for guarded-transition tests."""

    class State(models.TextChoices):
        """Finite states for the transition tests."""

        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        DONE = "done", "Done"
        ARCHIVED = "archived", "Archived"

    state = StateField(choices_enum=State, default=State.DRAFT)
    ready = models.BooleanField(default=True)
    owner = models.CharField(max_length=32, default="owner")
    note = models.CharField(max_length=64, blank=True)

    state_transitions = StateTransitions(
        state,
        {
            State.DRAFT: [State.RUNNING],
            State.RUNNING: [State.DONE],
            State.PAUSED: [State.DONE],
            ANY: [State.ARCHIVED],
        },
    )

    class Meta:
        """Model options for the test model."""

        app_label = "tests"

    @transition(
        state,
        source=State.DRAFT,
        target=State.RUNNING,
        conditions=[is_ready],
        permission=actor_is_owner,
        on_success=remember_success,
    )
    def mark_running(self, *, note: str = "") -> str:
        """Record body execution before the primitive writes the target state."""

        self.note = note
        self.body_state = self.state
        return "started"

    @transition(state, source=[State.RUNNING, State.PAUSED], target=State.DONE)
    def mark_done(self) -> None:
        """Move a running or paused row to done."""

        self.body_state = self.state

    @transition(state, source=ANY, target=State.ARCHIVED)
    def archive(self) -> None:
        """Archive from any source state."""


@pytest.fixture
def transition_task_table() -> Iterator[None]:
    """Create the throwaway table for one test."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(TransitionTask)
    try:
        yield
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(TransitionTask)


@pytest.mark.django_db(transaction=True)
def test_allowed_and_blocked_transitions(transition_task_table: None) -> None:
    """A declared transition runs once from its allowed source."""

    task = TransitionTask.objects.create()
    task.success_events = []

    result = task.mark_running(note="started by test")

    assert result == "started"
    assert task.state == TransitionTask.State.RUNNING
    assert task.body_state == TransitionTask.State.DRAFT
    assert task.note == "started by test"

    with pytest.raises(TransitionNotAllowed):
        task.mark_running()


@pytest.mark.django_db(transaction=True)
def test_wildcard_source_allows_any_current_state(transition_task_table: None) -> None:
    """A wildcard-source transition can start from every state."""

    running = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)
    draft = TransitionTask.objects.create()

    running.archive()
    draft.archive()

    assert running.state == TransitionTask.State.ARCHIVED
    assert draft.state == TransitionTask.State.ARCHIVED


@pytest.mark.django_db(transaction=True)
def test_condition_false_blocks_transition(transition_task_table: None) -> None:
    """A false condition prevents the method body and target write."""

    task = TransitionTask.objects.create(ready=False)
    task.success_events = []

    with pytest.raises(TransitionNotAllowed):
        task.mark_running()

    assert task.state == TransitionTask.State.DRAFT
    assert not hasattr(task, "body_state")
    assert task.success_events == []


@pytest.mark.django_db(transaction=True)
def test_permission_query_reports_without_blocking_execution(transition_task_table: None) -> None:
    """Permission is exposed as a query and is not part of method execution."""

    task = TransitionTask.objects.create(owner="owner")
    task.success_events = []

    assert task.has_transition_perm("mark_running", "owner") is True
    assert task.has_transition_perm("mark_running", "other") is False

    task.mark_running()

    assert task.state == TransitionTask.State.RUNNING


@pytest.mark.django_db(transaction=True)
def test_on_success_fires_with_instance_source_and_target(transition_task_table: None) -> None:
    """The explicit success hook receives the instance, source, and target."""

    task = TransitionTask.objects.create()
    task.success_events = []

    task.mark_running()

    assert task.success_events == [
        (task, TransitionTask.State.DRAFT, TransitionTask.State.RUNNING),
    ]


@pytest.mark.django_db(transaction=True)
def test_direct_assignment_rejected_on_guarded_fields(transition_task_table: None) -> None:
    """Guarded state columns cannot be changed by assignment."""

    task = TransitionTask.objects.create()

    with pytest.raises(TransitionNotAllowed):
        task.state = TransitionTask.State.RUNNING

    assert task.state == TransitionTask.State.DRAFT


@pytest.mark.django_db(transaction=True)
def test_transition_not_allowed_message_names_field_source_and_target(transition_task_table: None) -> None:
    """Illegal transition errors include the field, source, and target."""

    task = TransitionTask.objects.create(state=TransitionTask.State.DONE)

    with pytest.raises(TransitionNotAllowed) as error:
        task.mark_running()

    message = str(error.value)
    assert "state" in message
    assert "done" in message
    assert "running" in message
