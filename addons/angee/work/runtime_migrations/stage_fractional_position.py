"""Preserve queue-stage order while adopting unique fractional positions.

Only positions change: primary keys, public identities, relations and categories
remain intact. Reverse is deliberately refused: later fractional edits cannot be
safely cast back to integer positions. The old stable order is (queue, position, sqid).
"""

from django.db import migrations, models, router

from angee.base.fields.ordering import FractionalRankField


def applies(state):
    stage = state.models.get(("work", "stage"))
    if stage is None:
        return False
    position = stage.fields.get("position")
    if isinstance(position, FractionalRankField):
        return False
    if not isinstance(position, models.PositiveIntegerField):
        raise ValueError("work.Stage has an unexpected position field before fractional ordering.")
    return True


def forwards(apps, schema_editor):
    stage = apps.get_model("work", "Stage")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, stage):
        return
    rows = stage._base_manager.using(alias)
    # Snapshot the former order before changing the indexed sort column.
    identities = list(rows.order_by("queue_id", "position", "sqid").values_list("pk", "queue_id"))
    previous_container = None
    previous_rank = None
    batch = []
    for pk, container in identities:
        if container != previous_container:
            previous_rank = None
        rank = FractionalRankField.get_append_rank(previous_rank)
        batch.append(stage(pk=pk, position=rank))
        previous_container, previous_rank = container, rank
        if len(batch) == 500:
            rows.bulk_update(batch, ["position"])
            batch = []
    if batch:
        rows.bulk_update(batch, ["position"])


class Migration(migrations.Migration):
    dependencies = [("work", "__latest__")]
    operations = [
        migrations.AlterField(model_name="stage", name="position", field=FractionalRankField()),
        migrations.RunPython(forwards),
        migrations.AddConstraint(
            model_name="stage",
            constraint=models.UniqueConstraint(
                fields=("queue", "position"), name="uq_work_stage_queue_position",
            ),
        ),
    ]
