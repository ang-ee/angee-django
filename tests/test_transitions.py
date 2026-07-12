"""Tests for guarded ``StateField`` transitions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test import override_settings

from angee.base.fields import StateField
from angee.base.transitions import StateTransitions, TransitionNotAllowed, save_state, transition

POLICY_SETTING = "ANGEE_TEST_TRANSITION_POLICY"


def is_ready(instance: Any) -> bool:
    """Return whether the row can leave draft."""

    return bool(instance.ready)


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
    note = models.CharField(max_length=64, blank=True)

    state_transitions = StateTransitions(
        state,
        {
            State.DRAFT: [State.RUNNING],
            State.RUNNING: [State.DONE],
            State.PAUSED: [State.DONE],
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

    @transition(state, source=State.RUNNING, target=State.DONE, on_success=save_state)
    def persist_done(self) -> None:
        """Move a running row to done and save touched fields."""

        self.note = "persisted"
        self._transition_fields = {"note"}


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
def test_on_success_fires_with_instance_source_and_target(transition_task_table: None) -> None:
    """The explicit success hook receives the instance, source, and target."""

    task = TransitionTask.objects.create()
    task.success_events = []

    task.mark_running()

    assert task.success_events == [
        (task, TransitionTask.State.DRAFT, TransitionTask.State.RUNNING),
    ]


@pytest.mark.django_db(transaction=True)
def test_save_state_persists_transition_and_touched_fields(transition_task_table: None) -> None:
    """The shared save hook writes the guarded state plus method-touched fields."""

    task = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)

    task.persist_done()

    task.refresh_from_db()
    assert task.state == TransitionTask.State.DONE
    assert task.note == "persisted"


@pytest.mark.django_db(transaction=True)
def test_save_state_loses_a_concurrent_transition_race(transition_task_table: None) -> None:
    """Two racing transitions: the one whose committed source already moved loses.

    A concurrent transition is simulated by advancing the committed row behind the
    stale instance's back. The stale instance still reads ``running`` in memory, so
    it clears the source/graph checks and runs its body — but the compare-and-swap
    persist finds the committed state already ``done`` and raises rather than
    double-applying the transition. The losing body's touched fields never persist.
    """

    task = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)
    # A racer commits the running -> done transition first, behind this instance.
    TransitionTask.objects.filter(pk=task.pk).update(state=TransitionTask.State.DONE)

    with pytest.raises(TransitionNotAllowed):
        task.persist_done()

    task.refresh_from_db()
    assert task.state == TransitionTask.State.DONE
    assert task.note == ""


@pytest.mark.django_db(transaction=True)
def test_save_state_wins_when_the_committed_source_still_holds(transition_task_table: None) -> None:
    """The winning racer — the one whose committed source still holds — persists once."""

    task = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)

    task.persist_done()

    task.refresh_from_db()
    assert task.state == TransitionTask.State.DONE
    assert task.note == "persisted"


@pytest.mark.django_db(transaction=True)
def test_force_state_bypasses_graph_and_persists_with_save_state_guard(transition_task_table: None) -> None:
    """The public escape hatch can force a data-dependent target outside the graph."""

    task = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)
    task.note = "forced"
    task._transition_fields = {"note"}

    task.state_transitions.force_state(task, TransitionTask.State.ARCHIVED, reason="test force")

    task.refresh_from_db()
    assert task.state == TransitionTask.State.ARCHIVED
    assert task.note == "forced"


@pytest.mark.django_db(transaction=True)
def test_force_state_reuses_the_concurrency_guard(transition_task_table: None) -> None:
    """A force-state write still loses when the committed source changed first."""

    task = TransitionTask.objects.create(state=TransitionTask.State.RUNNING)
    TransitionTask.objects.filter(pk=task.pk).update(state=TransitionTask.State.DONE)
    task._transition_fields = set()

    with pytest.raises(TransitionNotAllowed, match="concurrent transition"):
        task.state_transitions.force_state(task, TransitionTask.State.ARCHIVED, reason="test stale force")

    assert TransitionTask.objects.get(pk=task.pk).state == TransitionTask.State.DONE


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


@pytest.mark.django_db(transaction=True)
def test_not_allowed_public_helper_raises_the_transition_error(transition_task_table: None) -> None:
    """Consumers can raise the primitive's standard error without private reaches."""

    task = TransitionTask.objects.create()

    with pytest.raises(TransitionNotAllowed) as error:
        task.state_transitions.not_allowed(task.state, TransitionTask.State.ARCHIVED)

    assert "state transition from draft to archived" in str(error.value)


class PolicyTask(models.Model):
    """Throwaway model exercising the settings-backed transition policy overlay."""

    class State(models.TextChoices):
        """Finite states for the policy-overlay tests."""

        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    state = StateField(choices_enum=State, default=State.DRAFT)

    state_transitions = StateTransitions(
        state,
        {State.DRAFT: [State.POSTED, State.CANCELLED]},
        policy_setting=POLICY_SETTING,
    )

    class Meta:
        """Model options for the policy test model."""

        app_label = "tests"

    @transition(state, source=State.DRAFT, target=State.POSTED)
    def post(self) -> None:
        """Post a draft row — a declared edge."""

    @transition(state, source=State.DRAFT, target=State.CANCELLED)
    def cancel(self) -> None:
        """Cancel a draft row — a declared edge a deny overlay can disable."""

    @transition(state, source=State.POSTED, target=State.DRAFT, policy="posted->draft")
    def reopen(self) -> None:
        """Reopen a posted row — a policy edge, absent from the declared graph."""


@pytest.fixture
def policy_task_table() -> Iterator[None]:
    """Create the throwaway policy table for one test."""

    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(PolicyTask)
    try:
        yield
    finally:
        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(PolicyTask)


@pytest.mark.django_db(transaction=True)
def test_policy_absent_uses_declared_graph(policy_task_table: None) -> None:
    """With no overlay set, declared edges run and the policy edge stays disabled."""

    task = PolicyTask.objects.create()
    task.post()
    assert task.state == PolicyTask.State.POSTED

    with pytest.raises(TransitionNotAllowed):
        task.reopen()
    assert task.state == PolicyTask.State.POSTED


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: {"allow": [["posted", "draft"]]}})
def test_policy_allow_enables_edge_forbidden_by_graph(policy_task_table: None) -> None:
    """An ``allow`` overlay enables a transition the declared graph omits."""

    task = PolicyTask.objects.create(state=PolicyTask.State.POSTED)
    task.reopen()
    assert task.state == PolicyTask.State.DRAFT


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: {"deny": [["draft", "cancelled"]]}})
def test_policy_deny_disables_declared_edge(policy_task_table: None) -> None:
    """A ``deny`` overlay disables a transition the declared graph allows."""

    task = PolicyTask.objects.create()
    with pytest.raises(TransitionNotAllowed):
        task.cancel()
    assert task.state == PolicyTask.State.DRAFT

    # A declared edge the overlay leaves untouched still runs.
    task.post()
    assert task.state == PolicyTask.State.POSTED


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: [["posted", "draft"]]})
def test_policy_overlay_must_be_a_mapping(policy_task_table: None) -> None:
    """A non-mapping overlay fails fast, naming the setting, not mid-transition."""

    task = PolicyTask.objects.create()
    with pytest.raises(ImproperlyConfigured, match=POLICY_SETTING):
        task.post()


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: {"allow": "posted->draft"}})
def test_policy_overlay_edge_list_must_be_a_list(policy_task_table: None) -> None:
    """A string where an edge list is expected is rejected, not iterated char-wise."""

    task = PolicyTask.objects.create()
    with pytest.raises(ImproperlyConfigured, match="allow"):
        task.post()


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: {"allow": [["posted", "draft", "extra"]]}})
def test_policy_overlay_edge_must_be_a_pair(policy_task_table: None) -> None:
    """A malformed edge (not a [source, target] pair) is rejected with a clear error."""

    task = PolicyTask.objects.create()
    with pytest.raises(ImproperlyConfigured, match="pair"):
        task.post()


@pytest.mark.django_db(transaction=True)
@override_settings(**{POLICY_SETTING: {"deny": [["draft", "bogus"]]}})
def test_policy_overlay_rejects_unknown_state(policy_task_table: None) -> None:
    """An edge naming a state outside the field's enum is rejected by name."""

    task = PolicyTask.objects.create()
    with pytest.raises(ImproperlyConfigured, match="unknown state"):
        task.post()


def test_policy_marker_requires_a_policy_setting() -> None:
    """A ``policy`` transition on a declaration without ``policy_setting`` fails fast."""

    with pytest.raises(ImproperlyConfigured):

        class Unresolvable(models.Model):
            """Model whose policy edge has no settings key to resolve it."""

            class State(models.TextChoices):
                """States for the misconfigured policy model."""

                DRAFT = "draft", "Draft"
                POSTED = "posted", "Posted"

            state = StateField(choices_enum=State, default=State.DRAFT)
            state_transitions = StateTransitions(state, {State.DRAFT: [State.POSTED]})

            class Meta:
                """Model options for the misconfigured policy model."""

                app_label = "tests"

            @transition(state, source=State.POSTED, target=State.DRAFT, policy="posted->draft")
            def reopen(self) -> None:
                """A policy edge with no ``policy_setting`` to govern it."""


def test_action_specs_project_declared_transitions_in_method_name_order() -> None:
    """Action specs are a frozen public view over transition methods."""

    specs = StateTransitions.action_specs(PolicyTask)

    assert tuple(spec.name for spec in specs) == ("cancel", "post", "reopen")
    assert specs == (
        specs[0].__class__(
            name="cancel",
            field="state",
            sources=("draft",),
            target="cancelled",
            policy=None,
        ),
        specs[0].__class__(
            name="post",
            field="state",
            sources=("draft",),
            target="posted",
            policy=None,
        ),
        specs[0].__class__(
            name="reopen",
            field="state",
            sources=("posted",),
            target="draft",
            policy="posted->draft",
        ),
    )
    with pytest.raises(AttributeError):
        specs[0].name = "mutated"  # type: ignore[misc]
