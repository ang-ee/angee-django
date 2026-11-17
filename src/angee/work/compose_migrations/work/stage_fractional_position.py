"""Migration template: preserve stage order while upgrading legacy tied integer positions.

Copy this module into the host's ``runtime/work/migrations/`` under the host's next
migration name, then replace the placeholder dependency below with the host's own
explicit migration (never ``__latest__``). A host whose ``work.Stage`` already carries
``uq_work_stage_queue_position`` has no legacy positions and skips this template.

Within each queue, stages are renumbered in their current order (position, then pk) at
``FractionalRankField.STEP`` intervals, so tied positions become distinct before the
per-queue uniqueness constraint is added. The reverse keeps the spread positions, which
are valid integer positions on the earlier release.
"""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from angee.base.fields.ordering import FractionalRankField


def spread_stages(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Renumber each queue's stages in their current order, one rank step apart."""

    rows = apps.get_model("work", "Stage")._base_manager.using(schema_editor.connection.alias)
    context: object = object()
    index = 0
    for row in rows.order_by("queue_id", "position", "pk").iterator():
        if row.queue_id != context:
            context, index = row.queue_id, 0
        index += 1
        rows.filter(pk=row.pk).update(position=index * FractionalRankField.STEP)
    # Flush deferred FK checks from the updates before AddConstraint alters the table.
    schema_editor.connection.check_constraints()


class Migration(migrations.Migration):
    # Placeholder: the host's latest migration of ``work.Stage`` with its integer ``position``.
    dependencies = [("work", "HOST_MIGRATION_WITH_THE_INTEGER_STAGE_POSITION")]
    operations = [
        migrations.AlterField("stage", "position", FractionalRankField()),
        migrations.RunPython(spread_stages, migrations.RunPython.noop),
        migrations.AddConstraint(
            "stage", models.UniqueConstraint(fields=("queue", "position"), name="uq_work_stage_queue_position"),
        ),
    ]
