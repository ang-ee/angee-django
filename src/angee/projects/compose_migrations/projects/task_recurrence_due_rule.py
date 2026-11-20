"""Migration template: move each task's ``recurrence`` into a due rule that repeats, then retire the column.

Copy this module into the host's ``runtime/projects/migrations/`` under the host's next
migration name, then replace the placeholder dependency below with the host's own
explicit migration (never ``__latest__``); a host may instead subclass ``Migration``
here and declare its own ``dependencies``.

Before this release ``projects.Task.recurrence`` held an RFC-5545 RRULE that
nothing acted on; a task's repetition is now its due rule's ``repeat``
(``angee.scheduling.due``). The migration adds ``due_at``, ``due_rule`` and
``due_basis`` to the task table and its history table, moves the values, and
removes ``recurrence`` from both, which the drop guard refuses to remove by
autodetection while the column holds rules. Run ``makemigrations`` afterwards for
the release's other columns. The upgrade is **irreversible**: the removed RRULE
text is not restored.

A task with a recurrence keeps its date and takes the rule "the date set by
hand, repeating on the RRULE" (:func:`task_due_rule`). A calendar repeat starts
again at each due point, so a ``COUNT`` becomes the ``UNTIL`` of the last of
that many points, counting the task's date as the first (RFC 5545 counts
``DTSTART`` even when the rule would not produce it); a ``COUNT`` of one keeps no
repeat. A task without a date keeps its repeat, pending until someone sets a
date, and its ``COUNT`` counts from its creation day. A blank or malformed rule
is skipped and counted in the log; the task keeps no rule.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from typing import Any

from django.apps.registry import Apps
from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.utils import timezone

from angee.scheduling.due import DueRule, Repeat, Term
from angee.scheduling.fields import DueRuleField
from angee.scheduling.recurrence import Recurrence

logger = logging.getLogger(__name__)

_SERIES_END = datetime(9999, 12, 31, tzinfo=UTC)
_DUE_COLUMNS = (
    ("due_at", lambda: models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
    ("due_rule", lambda: DueRuleField(blank=True, default=None, null=True)),
    ("due_basis", lambda: models.JSONField(blank=True, default=dict, editable=False)),
)


def calendar_rule(rule: str, start: date) -> str | None:
    """Return ``rule`` with a ``COUNT`` made the ``UNTIL`` of its last point, ``start`` the first.

    ``None`` means no repeat.
    """

    parts = [part for part in rule.strip().split(";") if part]
    count = [part for part in parts if part.upper().startswith("COUNT=")]
    if not count:
        return ";".join(parts)
    total = int(count[-1].split("=", 1)[1])
    zone = timezone.get_default_timezone()
    dtstart = datetime.combine(start, time.min, zone)
    occurrences = Recurrence(rule).occurrences(dtstart, dtstart, _SERIES_END)
    # The due day is the first point; a day-grain repeat steps past its whole day.
    following = [moment for moment in occurrences if moment.astimezone(zone).date() > start]
    if total <= 1 or not following:
        return None
    last = following[min(total - 1, len(following)) - 1]
    kept = [part for part in parts if not part.upper().startswith("COUNT=")]
    return ";".join((*kept, f"UNTIL={last.astimezone(UTC):%Y%m%dT%H%M%SZ}"))


def task_due_rule(
    recurrence: str,
    *,
    due_date: date | None,
    created_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the due rule and basis replacing ``recurrence`` on a task due ``due_date``; ``None``: no rule.

    Raises ``ValidationError`` or ``ValueError`` for a malformed rule.
    """

    Recurrence(recurrence).validate()
    repeat = calendar_rule(recurrence, due_date or timezone.localtime(created_at).date())
    if repeat is None:
        return None
    rule = DueRule(terms=(Term(anchor="set"),), repeat=Repeat.from_rrule(repeat))
    return rule.as_json(), rule.resolve(anchors={"set": due_date}).as_json()


def task_recurrence_due_rule(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Give each task with a recurrence the due rule replacing it; blank or malformed rules are skipped and logged."""

    # The historical task still has ``recurrence``, which the current model class does not.
    task_model: Any = apps.get_model("projects", "Task")
    tasks = task_model._base_manager.using(schema_editor.connection.alias)
    skipped = 0
    for task in tasks.exclude(recurrence="").order_by("pk").iterator():
        if not task.recurrence.strip():
            skipped += 1
            continue
        try:
            moved = task_due_rule(task.recurrence, due_date=task.due_date, created_at=task.created_at)
        except ValidationError, ValueError:
            skipped += 1
            continue
        if moved is not None:
            rule, basis = moved
            tasks.filter(pk=task.pk).update(due_rule=rule, due_basis=basis)
    if skipped:
        logger.warning("projects.task_recurrence_due_rule skipped %d blank or malformed recurrence rules.", skipped)


class Migration(migrations.Migration):
    # Placeholder: the host's latest ``projects`` migration, whose task still has ``recurrence``.
    dependencies = [("projects", "HOST_MIGRATION_WITH_THE_TASK_RECURRENCE_COLUMN")]
    operations = [
        *(
            migrations.AddField(table, name, field())
            for table in ("task", "historicaltask")
            for name, field in _DUE_COLUMNS
        ),
        migrations.RunPython(task_recurrence_due_rule),
        migrations.RemoveField("task", "recurrence"),
        migrations.RemoveField("historicaltask", "recurrence"),
    ]
