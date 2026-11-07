"""Work campaign fixtures reuse the productivity suite's concrete compositions."""

from contextlib import nullcontext

import pytest
from rebac import actor_context, system_context

from tests.test_productivity_write_behavior import CreateTask, Stage
from tests.test_productivity_write_behavior import Queue as Queue
from tests.test_productivity_write_behavior import productivity_create_case as productivity_create_case


@pytest.fixture
def work_case(productivity_create_case):
    _, actor, queue = productivity_create_case
    with system_context(reason="test.work.campaign.setup"):
        queue.triage_enabled = True
        queue.save(update_fields=["triage_enabled"])
    return actor, queue


def make_task(queue, *, category="unstarted", **fields):
    """Set up a persisted source stage, including seed/rule-owned states."""
    with system_context(reason="test.work.campaign.task"):
        stage = Stage._base_manager.filter(queue=queue, category=category).first() if category else None
        return CreateTask.objects.create(title="Work item", queue=queue, stage=stage, **fields)


def hand_context(actor, elevated):
    """Exercise hand-stage operations with and without ambient authorization bypass."""
    return system_context(reason="test.work.campaign.hand") if elevated else actor_context(actor)


def invoke(task, verb, *, canonical=None, stage=None):
    """Supply only each verb's native arguments; production owns its decisions."""
    if verb == "drop":
        return task.drop("obsolete")
    if verb == "drop_duplicate":
        return task.drop("duplicate")
    if verb == "decline":
        return task.decline("declined")
    if verb == "mark_duplicate":
        return task.mark_duplicate(canonical)
    if verb == "accept":
        return task.accept(stage)
    return getattr(task, verb)()


HAND_VERBS = ("start", "complete", "reopen", "accept", "decline", "drop", "drop_duplicate",
              "return_to_triage", "mark_duplicate")


def persisted_state(task):
    """Capture persisted lifecycle facts so failed operations prove atomicity."""
    return CreateTask._base_manager.filter(pk=task.pk).values(
        "queue_id", "stage_id", "status", "done_at", "dropped_at", "dropped_reason",
        "started_triage_at", "triaged_at", "updated_at", "estimate",
    ).get()


def rule_context(task, enabled):
    return task._work_verb_write() if enabled else nullcontext()
