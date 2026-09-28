"""Backfill ownership only for threads without a channel."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    thread = project_state.models.get(("messaging", "thread"))
    if thread is None or "owner" in thread.fields:
        return False
    if "created_by" not in thread.fields:
        raise ValueError("Thread ownership requires the existing created_by attribution column.")
    return True


def forwards(apps, schema_editor):
    rows = apps.get_model("messaging", "Thread")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(channel_id__isnull=True).update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "thread",
            "owner",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=audit_set_null,
                related_name="+",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
