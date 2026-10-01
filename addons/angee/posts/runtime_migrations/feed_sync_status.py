"""Carry Feed's last sync outcome into its enum-backed sync stage.

Declared before ``feed_channel_parent`` in the manifest: that migration moves Feed
under Channel without removing ``last_sync_status``, so the status must be carried
and the column dropped first.
"""

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
    model = project_state.models.get(("posts", "feed"))
    if model is None:
        return False
    fields = model.fields
    if "sync_stage" not in fields:
        if "last_sync_status" not in fields:
            return False
        raise ValueError("Feed has no sync stage in its migration state.")
    stage = fields["sync_stage"]
    if "last_sync_status" in fields and type(stage) is models.CharField:
        return True
    if "last_sync_status" not in fields and isinstance(stage, StateField):
        return False
    raise ValueError("Feed has a partial sync status transition; complete or reverse its migration history.")


def carry_sync_failure(apps, schema_editor):
    rows = apps.get_model("posts", "Feed")._base_manager.using(schema_editor.connection.alias).order_by()
    if rows.exclude(sync_stage__in=[value for value, _ in STAGES]).exists():
        raise ValueError("Feed has a sync stage outside the SyncStage enum.")
    if rows.exclude(last_sync_status__in=["", "ok", "error"]).exists():
        raise ValueError("Feed has an unknown last sync status.")
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
            "feed",
            "sync_stage",
            StateField(choices=STAGES, max_length=32, default="idle", db_index=True),
        ),
        migrations.RemoveField("feed", "last_sync_status"),
    ]
