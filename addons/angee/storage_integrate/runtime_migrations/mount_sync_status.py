"""Carry Mount's last sync outcome into its enum-backed sync stage."""

from django.db import migrations, models

from angee.base.fields import StateField


STAGES = (
    ("idle", "Idle"),
    ("queued", "Queued"),
    ("discovering", "Discovering"),
    ("syncing", "Syncing"),
    ("completed", "Completed"),
    ("failed", "Failed"),
)


def applies(project_state):
    model = project_state.models.get(("storage_integrate", "mount"))
    if model is None:
        return False
    fields = model.fields
    if "sync_stage" not in fields:
        if "last_sync_status" not in fields:
            return False
        raise ValueError("Mount has no sync stage in its migration state.")
    stage = fields["sync_stage"]
    if "last_sync_status" in fields and type(stage) is models.CharField:
        return True
    if "last_sync_status" not in fields and isinstance(stage, StateField):
        return False
    raise ValueError("Mount has a partial sync status transition; complete or reverse its migration history.")


def carry_sync_failure(apps, schema_editor):
    rows = apps.get_model("storage_integrate", "Mount")._base_manager.using(schema_editor.connection.alias).order_by()
    if rows.exclude(sync_stage__in=[value for value, _ in STAGES]).exists():
        raise ValueError("Mount has a sync stage outside the SyncStage enum.")
    if rows.exclude(last_sync_status__in=["", "ok", "error"]).exists():
        raise ValueError("Mount has an unknown last sync status.")
    # The AlterField and RemoveField that follow change this same table. PostgreSQL
    # must check foreign keys of the updated rows now or it refuses that DDL.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    rows.filter(last_sync_status="error").update(sync_stage="failed")


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RunPython(carry_sync_failure, migrations.RunPython.noop),
        migrations.AlterField(
            "mount",
            "sync_stage",
            StateField(choices=STAGES, max_length=32, default="idle", db_index=True),
        ),
        migrations.RemoveField("mount", "last_sync_status"),
    ]
