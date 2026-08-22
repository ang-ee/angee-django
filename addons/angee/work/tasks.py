"""Celery entrypoints for work-owned scheduled maintenance."""

from __future__ import annotations

from datetime import UTC, datetime

from celery import shared_task
from django.apps import apps
from django.utils import timezone
from rebac import system_context


def _periodic_now(timestamp: int | None) -> datetime:
    """Return an aware instant from Celery's optional periodic timestamp."""

    return timezone.now() if timestamp is None else datetime.fromtimestamp(timestamp, tz=UTC)


@shared_task(name="work.wake_due_snoozes")
def wake_due_snoozes(timestamp: int | None = None) -> int:
    """Clear every task snooze whose inclusive wake instant has arrived."""

    task_model = apps.get_model("projects", "Task")
    with system_context(reason="work.tasks.wake_due_snoozes"):
        return int(task_model.wake_due_snoozes(now=_periodic_now(timestamp)))


@shared_task(name="work.generate_cycles")
def generate_cycles(timestamp: int | None = None) -> int:
    """Maintain generated cadence and close every cycle whose end day passed."""

    now = _periodic_now(timestamp)
    as_of = timezone.localtime(now).date()
    queue_model = apps.get_model("work", "Queue")
    cycle_model = apps.get_model("work", "Cycle")
    generated = 0
    with system_context(reason="work.tasks.generate_cycles"):
        queues = queue_model.objects.sudo(reason="work.tasks.generate_cycles.queues").filter(
            cycles_enabled=True
        ).order_by("pk")
        for queue in queues:
            before = cycle_model._base_manager.filter(queue=queue).count()
            cycle_model.objects.generate_for_queue(queue, as_of=as_of, completed_at=now)
            generated += cycle_model._base_manager.filter(queue=queue).count() - before
    return generated
