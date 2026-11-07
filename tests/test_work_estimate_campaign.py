"""Queue estimate policy applies to writes, using current persisted settings."""

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from tests.work_campaign import CreateTask, Queue, hand_context, make_task, rule_context
from tests.work_campaign import productivity_create_case as productivity_create_case
from tests.work_campaign import work_case as work_case


@pytest.mark.parametrize("scale", Queue.EstimateScale.values)
def test_creation_default_obeys_scale_and_preserves_explicit_fraction(work_case, scale):
    actor, queue = work_case
    with actor_context(actor):
        queue.estimate_scale, queue.default_estimate = scale, 1.25
        queue.save(update_fields=["estimate_scale", "default_estimate"])
        defaulted = CreateTask.objects.create(title="Default", queue=queue)
        explicit = CreateTask.objects.create(title="Fraction", queue=queue, estimate=0.75)
        assert defaulted.estimate == (None if scale == "none" else 1.25)
        assert explicit.estimate == 0.75
        defaulted.estimate = None
        defaulted.save(update_fields=["estimate"])
        defaulted.refresh_from_db()
        assert defaulted.estimate is None


@pytest.mark.parametrize("method", ["clean", "save"])
def test_queue_refuses_zero_default_when_zero_is_disallowed(work_case, method):
    actor, queue = work_case
    queue.estimate_allow_zero, queue.default_estimate = False, 0
    with actor_context(actor), pytest.raises(ValidationError, match="Default estimate cannot be zero"):
        getattr(queue, method)()
    queue.refresh_from_db()
    assert queue.estimate_allow_zero is True
    assert queue.default_estimate is None


@pytest.mark.parametrize("allowed", [False, True])
def test_zero_insert_uses_current_policy_even_with_cached_queue(work_case, allowed):
    actor, queue = work_case
    changed = Queue._base_manager.get(pk=queue.pk)
    with system_context(reason="test.work.estimate.policy"):
        changed.estimate_allow_zero = allowed
        changed.save(update_fields=["estimate_allow_zero"])
    with actor_context(actor):
        if allowed:
            task = CreateTask.objects.create(title="Zero", queue=queue, estimate=0)
            assert task.estimate == 0
        else:
            with pytest.raises(ValidationError, match="zero estimate"):
                CreateTask.objects.create(title="Zero", queue=queue, estimate=0)
            assert not CreateTask._base_manager.exists()


@pytest.mark.parametrize("mode", ["full", "partial", "deferred"])
@pytest.mark.parametrize("allowed", [False, True])
def test_changed_estimate_reads_tightened_or_relaxed_cached_policy(work_case, mode, allowed):
    actor, queue = work_case
    with system_context(reason="test.work.estimate.initial"):
        queue.estimate_allow_zero = not allowed
        queue.save(update_fields=["estimate_allow_zero"])
    task = make_task(queue, estimate=2)
    if mode == "deferred":
        task = CreateTask._base_manager.only("pk", "title").get(pk=task.pk)
    assert task.queue.estimate_allow_zero is not allowed
    with system_context(reason="test.work.estimate.changed"):
        Queue._base_manager.filter(pk=queue.pk).update(estimate_allow_zero=allowed)
    task.estimate = 0
    with actor_context(actor):
        if allowed:
            task.save(**({"update_fields": ["estimate"]} if mode == "partial" else {}))
        else:
            with pytest.raises(ValidationError, match="zero estimate"):
                task.save(**({"update_fields": ["estimate"]} if mode == "partial" else {}))
    assert CreateTask._base_manager.get(pk=task.pk).estimate == (0 if allowed else 2)


@pytest.mark.parametrize("mode", ["full", "partial", "deferred", "clean", "rule", "sudo"])
def test_existing_zero_survives_unrelated_writes_after_policy_tightens(work_case, mode):
    actor, queue = work_case
    task = make_task(queue, estimate=0)
    with system_context(reason="test.work.estimate.tighten"):
        Queue._base_manager.filter(pk=queue.pk).update(estimate_allow_zero=False)
    if mode == "deferred":
        task = CreateTask._base_manager.only("pk", "title").get(pk=task.pk)
    with hand_context(actor, mode == "sudo"), rule_context(task, mode == "rule"):
        task.title = "Still writable"
        if mode == "clean":
            task.clean()
        task.save(**({"update_fields": ["title"]} if mode == "partial" else {}))
    task.refresh_from_db()
    assert (task.title, task.estimate) == ("Still writable", 0)


@pytest.mark.parametrize("partial", [False, True])
def test_queue_change_revalidates_an_existing_zero(work_case, partial):
    actor, queue = work_case
    task = make_task(queue, estimate=0)
    with system_context(reason="test.work.estimate.destination"):
        other = Queue.objects.create(key="OTHER", slug="other", name="Other", estimate_allow_zero=False)
    original_queue, original_stage = task.queue_id, task.stage_id
    task.queue, task.stage = other, other.default_stage
    with actor_context(actor), pytest.raises(ValidationError, match="zero estimate"):
        task.save(**({"update_fields": ["queue", "stage"]} if partial else {}))
    task.refresh_from_db()
    assert (task.queue_id, task.stage_id, task.estimate) == (original_queue, original_stage, 0)


def test_partial_save_does_not_consume_an_unsaved_estimate_edit(work_case):
    actor, queue = work_case
    task = make_task(queue, estimate=1)
    with system_context(reason="test.work.estimate.tighten"):
        Queue._base_manager.filter(pk=queue.pk).update(estimate_allow_zero=False)
    with actor_context(actor):
        task.estimate, task.title = 0, "Title only"
        task.save(update_fields=["title"])
        assert CreateTask._base_manager.get(pk=task.pk).estimate == 1
        with pytest.raises(ValidationError, match="zero estimate"):
            task.save(update_fields=["estimate"])


@pytest.mark.parametrize("scale", ["none", "hours"])
def test_creation_refreshes_both_cached_scale_and_default(work_case, scale):
    actor, queue = work_case
    queue.estimate_scale, queue.default_estimate = "days", 2
    with system_context(reason="test.work.estimate.cached"):
        queue.save(update_fields=["estimate_scale", "default_estimate"])
        Queue._base_manager.filter(pk=queue.pk).update(estimate_scale=scale, default_estimate=0.5)
    with actor_context(actor):
        task = CreateTask.objects.create(title="Current default", queue=queue)
    assert task.estimate == (None if scale == "none" else 0.5)
