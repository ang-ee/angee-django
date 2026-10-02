"""Reservations follow persisted stages across stale and partial task saves."""

from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from tests.work_campaign import CreateTask, Stage, make_task, persisted_state
from tests.work_campaign import productivity_create_case as productivity_create_case
from tests.work_campaign import work_case as work_case


@pytest.mark.parametrize("field", ["stage", "stage_id"])
@pytest.mark.parametrize("direction", ["enter", "leave"])
def test_direct_stage_write_refuses_rule_owned_entry_and_exit(work_case, field, direction):
    actor, queue = work_case
    task = make_task(queue)
    with system_context(reason="test.work.rule_stage"):
        reserved = Stage.objects.create(queue=queue, name="Rule", category="completed", rule_owned=True)
        if direction == "leave":
            task.stage = reserved
            task.save(update_fields=["stage"])
    before = persisted_state(task)
    target = reserved if direction == "enter" else queue.default_stage
    setattr(task, field, target if field == "stage" else target.pk)
    with actor_context(actor), pytest.raises(ValidationError, match="rule-owned"):
        task.save(update_fields=[field])
    assert persisted_state(task) == before


def test_stale_task_cannot_leave_a_stage_that_a_rule_entered(work_case):
    actor, queue = work_case
    stale = make_task(queue)
    with system_context(reason="test.work.concurrent_rule"):
        reserved = Stage.objects.create(queue=queue, name="Rule", category="completed", rule_owned=True)
        fresh = CreateTask._base_manager.get(pk=stale.pk)
        fresh.stage = reserved
        with fresh._work_verb_write():
            fresh.save(update_fields=["stage"])
    before = persisted_state(stale)
    stale.stage = Stage._base_manager.get(queue=queue, category="started")
    with actor_context(actor), pytest.raises(ValidationError, match="out of a rule-owned stage"):
        stale.save(update_fields=["stage"])
    assert persisted_state(stale) == before


@pytest.mark.parametrize("loaded_stage", ["ordinary", "null"])
def test_partial_save_uses_persisted_stage_for_guard_and_projection(work_case, loaded_stage, monkeypatch):
    actor, queue = work_case
    stale = make_task(queue)
    if loaded_stage == "null":
        with system_context(reason="test.work.legacy_null"):
            CreateTask._base_manager.filter(pk=stale.pk).update(stage=None)
        stale = CreateTask._base_manager.get(pk=stale.pk)
    monkeypatch.setattr("angee.work.models.timezone.now", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    with system_context(reason="test.work.rule_transition"):
        reserved = Stage.objects.create(queue=queue, name="Rule", category="completed", rule_owned=True)
        fresh = CreateTask._base_manager.get(pk=stale.pk)
        fresh.stage = reserved
        fresh.save(update_fields=["stage"])
    before = persisted_state(stale)
    monkeypatch.setattr("angee.work.models.timezone.now", lambda: datetime(2026, 1, 2, tzinfo=UTC))
    with actor_context(actor):
        stale.title = "Unrelated change"
        stale.save(update_fields=["title"])
    after = persisted_state(stale)
    # An edit updates audit time, but must retain the rule's completion receipt.
    before.pop("updated_at")
    after.pop("updated_at")
    assert after == before
    assert CreateTask._base_manager.get(pk=stale.pk).title == "Unrelated change"


def test_partial_save_of_null_stage_does_not_write_inferred_default(work_case):
    actor, queue = work_case
    task = make_task(queue)
    with system_context(reason="test.work.legacy_null"):
        CreateTask._base_manager.filter(pk=task.pk).update(stage=None)
    task = CreateTask._base_manager.get(pk=task.pk)
    with actor_context(actor):
        task.title = "Title only"
        task.save(update_fields=["title"])
    assert CreateTask._base_manager.get(pk=task.pk).stage_id is None


def test_cached_target_cannot_hide_new_rule_ownership(work_case):
    actor, queue = work_case
    task = make_task(queue, category="triage")
    cached = Stage._base_manager.get(queue=queue, category="started")
    with system_context(reason="test.work.target_reserved"):
        Stage._base_manager.filter(pk=cached.pk).update(rule_owned=True)
    before = persisted_state(task)
    with actor_context(actor), pytest.raises(ValidationError, match="rule-owned or concealing stage"):
        task.accept(cached)
    assert persisted_state(task) == before


def test_clearing_queue_and_stage_cannot_escape_rule_owned_stage(work_case):
    actor, queue = work_case
    task = make_task(queue, category="backlog")
    with system_context(reason="test.work.reserve_source"):
        Stage._base_manager.filter(pk=task.stage_id).update(rule_owned=True)
    before = persisted_state(task)
    task.queue = task.stage = None
    with actor_context(actor), pytest.raises(ValidationError, match="out of a rule-owned stage"):
        task.save(update_fields=["queue", "stage"])
    assert persisted_state(task) == before


def test_internal_rule_write_enters_and_leaves_while_same_stage_save_is_allowed(work_case):
    actor, queue = work_case
    task = make_task(queue)
    with system_context(reason="test.work.rule_stage"):
        reserved = Stage.objects.create(queue=queue, name="Rule", category="completed", rule_owned=True)
    with actor_context(actor):
        with task._work_verb_write():
            task.stage = reserved
            task.save(update_fields=["stage"])
        task.title = "Rule stage title"
        task.save(update_fields=["title", "stage"])
        assert task.status == "done"
        with task._work_verb_write():
            task.stage = queue.default_stage
            task.save(update_fields=["stage"])
    task.refresh_from_db()
    assert (task.status, task.done_at) == ("open", None)


def test_drop_cannot_hide_a_direct_status_assignment(work_case):
    actor, queue = work_case
    task = make_task(queue)
    before = persisted_state(task)
    task.status = "dropped"
    with actor_context(actor), pytest.raises(ValidationError, match="status"):
        task.drop("obsolete")
    assert persisted_state(task) == before
