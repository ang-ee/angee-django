"""Convert existing workflow concealment to the shared task trash owner.

Stage.conceals remains authored workflow intent. Its field help records the new
meaning in Django migration state; the old meaning selected the removed list.
This backfill waits for Projects to install shared trash columns and retains
stage values and every prior snapshot. The baseline has no invented changeset.
"""

from django.db import migrations, models, router
from django.utils import timezone

CONCEALS_HELP = "Tasks entering this stage move to shared trash."


def applies(state):
    stage = state.models.get(("work", "stage"))
    task = state.models.get(("projects", "task"))
    if stage is None or task is None or "conceals" not in stage.fields or "stage" not in task.fields:
        return False
    if stage.fields["conceals"].help_text == CONCEALS_HELP:
        return False
    # Projects' task_identity is an earlier composer round on existing stacks.
    return "is_trashed" in task.fields


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    task_model = apps.get_model("projects", "Task")
    if not router.allow_migrate_model(alias, task_model):
        return
    tasks = task_model._base_manager.using(alias)
    stages = apps.get_model("work", "Stage")._base_manager.using(alias)
    changed = list(
        tasks.filter(is_trashed=False, stage_id__in=stages.filter(conceals=True).order_by().values("pk"))
        .order_by("pk").values_list("pk", flat=True)
    )
    now = timezone.now()
    columns = {field.attname for field in tasks.model._meta.concrete_fields}
    for row in tasks.filter(pk__in=changed).order_by("pk").iterator():
        tasks.filter(pk=row.pk).update(
            is_trashed=True,
            trashed_at=row.updated_at if "updated_at" in columns else now,
            trashed_by_id=row.updated_by_id if "updated_by_id" in columns else None,
        )
    try:
        history = apps.get_model("projects", "HistoricalTask")
    except LookupError:
        return
    if not router.allow_migrate_model(alias, history):
        return
    copied = [
        field.attname
        for field in history._meta.concrete_fields
        if not field.name.startswith("history_") and field.name != "changeset" and field.attname in columns
    ]
    for row in tasks.filter(pk__in=changed).order_by("pk").iterator():
        history._base_manager.using(alias).create(
            **{name: getattr(row, name) for name in copied},
            history_date=now,
            history_type="~",
            history_user_id=None,
            history_change_reason="Consolidated workflow concealment in task trash",
        )


class Migration(migrations.Migration):
    dependencies = [("projects", "__latest__"), ("work", "__latest__")]
    operations = [
        migrations.AlterField("stage", "conceals", models.BooleanField(default=False, help_text=CONCEALS_HELP)),
        migrations.RunPython(forwards, hints={"model_name": "stage"}),
    ]
