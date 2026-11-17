"""Move each task's ``recurrence`` into a due rule that repeats, then retire the column.

Before this release ``projects.Task.recurrence`` held an RFC-5545 RRULE that
nothing acted on; a task's repetition is now its due rule's ``repeat``
(``angee.scheduling.due``). Author this upgrade as the host's next ``projects``
migration: it adds the task's ``due_at``, ``due_rule`` and ``due_basis``
columns, moves the values, and removes ``recurrence``, which the drop guard
refuses to remove by autodetection while the column holds rules. Run
``makemigrations`` afterwards for the release's other columns.

A task with a recurrence keeps its date and takes the rule "the date set by
hand, repeating on the RRULE" (:func:`task_due_rule`). A calendar repeat starts
again at each due point, so a ``COUNT`` becomes the ``UNTIL`` of the series'
last occurrence counted from the task's date (its creation day when it has
none). The basis is resolved by the release's due-rule owner, so the task
follows its rule.
"""

from datetime import UTC, date, datetime, time
from typing import Any

from django.db import migrations, models
from django.utils import timezone

from angee.scheduling.due import DueRule, Repeat, Term
from angee.scheduling.fields import DueRuleField
from angee.scheduling.recurrence import Recurrence

_SERIES_END = datetime(9999, 12, 31, tzinfo=UTC)


def applies(state):
    task = state.models.get(("projects", "task"))
    return task is not None and "recurrence" in task.fields


def calendar_rule(rule: str, start: date) -> str:
    """Return ``rule`` with a ``COUNT`` replaced by the ``UNTIL`` of its last occurrence from ``start``."""

    parts = [part for part in rule.strip().split(";") if part]
    count = [part for part in parts if part.upper().startswith("COUNT=")]
    if not count:
        return ";".join(parts)
    zone = timezone.get_default_timezone()
    dtstart = datetime.combine(start, time.min, zone)
    occurrences = Recurrence(rule).occurrences(dtstart, dtstart, _SERIES_END)
    kept = [part for part in parts if not part.upper().startswith("COUNT=")]
    last = occurrences[-1] if occurrences else dtstart
    return ";".join((*kept, f"UNTIL={last.astimezone(UTC):%Y%m%dT%H%M%SZ}"))


def task_due_rule(recurrence: str, *, due_date: date | None, created_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the due rule and basis replacing ``recurrence`` on a task due ``due_date``."""

    start = due_date or timezone.localtime(created_at).date()
    rule = DueRule.of(Term(anchor="set"), repeat=Repeat.calendar(calendar_rule(recurrence, start)))
    return rule.as_json(), rule.resolve(anchors={"set": due_date}).basis


def task_recurrence_due_rule(apps, schema_editor):
    tasks = apps.get_model("projects", "Task")._base_manager.using(schema_editor.connection.alias)
    for task in tasks.exclude(recurrence="").order_by("pk").iterator():
        rule, basis = task_due_rule(task.recurrence, due_date=task.due_date, created_at=task.created_at)
        tasks.filter(pk=task.pk).update(due_rule=rule, due_basis=basis)


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddField(
            "task", "due_at", models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
        ),
        migrations.AddField("task", "due_rule", DueRuleField(blank=True, default=None, null=True)),
        migrations.AddField("task", "due_basis", models.JSONField(blank=True, default=dict, editable=False)),
        migrations.RunPython(task_recurrence_due_rule, migrations.RunPython.noop),
        migrations.RemoveField("task", "recurrence"),
    ]
