"""Preserve stage order while upgrading legacy tied integer positions."""

from django.db import migrations, models

from angee.base.fields.ordering import FractionalRankField


def applies(state):
    stage = state.models.get(("work", "stage"))
    return stage is not None and not any(
        constraint.name == "uq_work_stage_queue_position" for constraint in stage.options.get("constraints", ())
    )


def spread_stages(apps, schema_editor):
    rows = apps.get_model("work", "Stage")._base_manager.using(schema_editor.connection.alias)
    context = object()
    index = 0
    for row in rows.order_by("queue_id", "position", "pk").iterator():
        if row.queue_id != context:
            context, index = row.queue_id, 0
        index += 1
        rows.filter(pk=row.pk).update(position=index * FractionalRankField.STEP)


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AlterField("stage", "position", FractionalRankField()),
        migrations.RunPython(spread_stages, migrations.RunPython.noop),
        migrations.AddConstraint(
            "stage", models.UniqueConstraint(fields=("queue", "position"), name="uq_work_stage_queue_position"),
        ),
    ]
