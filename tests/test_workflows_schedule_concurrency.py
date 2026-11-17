"""Real row-lock races between schedule passes, disabling and admission."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import system_context

from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import Trigger, TriggerEvent, WorkflowRun
from tests.lock_waits import submit, wait_for_lock
from tests.test_workflows_schedule import hourly
from tests.workflow_steps import document

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.fixture
def raced(execution, settings):
    """An hourly schedule that missed two occurrences, on UTC wall clocks."""
    settings.TIME_ZONE = "UTC"
    actor, _sent = execution
    workflow = load_workflow(document("entry"), key="raced-digest", actor=actor)
    return actor, workflow, hourly(workflow, actor, missed=2)


def test_a_pass_skips_a_schedule_another_transaction_holds_and_records_it_once_released(raced):
    """Two passes never record the same cursor: a busy schedule waits for the next tick."""
    _actor, workflow, trigger = raced
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.hold schedule"):
            Trigger.system_queryset().filter(pk=trigger.pk).select_for_update(no_key=True).get()
            busy, _pid = submit(pool, Trigger.objects.record_occurrences)
            assert busy.result(timeout=10) == 0
        released, _pid = submit(pool, Trigger.objects.record_occurrences)
        assert released.result(timeout=10) == 2
    assert TriggerEvent.system_queryset().filter(trigger=trigger).count() == 2
    assert Trigger.objects.record_occurrences() == 0
    assert Trigger.objects.drain() == 1
    assert WorkflowRun.system_queryset().filter(version__workflow=workflow).count() == 1


def test_disabling_waits_for_a_recording_pass_and_skips_what_it_recorded(raced):
    """Disable serializes behind the pass's trigger lock, so no recorded occurrence outlives it pending."""
    actor, workflow, trigger = raced
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.recording pass"):
            locked = Trigger.system_queryset().filter(pk=trigger.pk).select_for_update(no_key=True).get()
            disabling, pid = submit(pool, lambda: Trigger.objects.disable(trigger, actor=actor))
            wait_for_lock(pid, disabling)
            occurrences = locked.due_occurrences(locked.next_occurrence_at)
            TriggerEvent.objects.record_occurrences(locked, occurrences, supersede_before=occurrences[-1])
        assert not disabling.result(timeout=10).enabled
    assert TriggerEvent.system_queryset().filter(trigger=trigger).count() == 1
    assert not TriggerEvent.system_queryset().filter(trigger=trigger).pending().exists()
    assert Trigger.objects.drain() == 0
    assert not WorkflowRun.system_queryset().filter(version__workflow=workflow).exists()
