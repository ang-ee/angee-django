"""Separate note ownership from attribution while retaining existing access."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    note = project_state.models.get(("notes", "note"))
    if note is None or "owner" in note.fields:
        return False
    if "created_by" not in note.fields:
        raise ValueError("Note ownership requires the existing created_by attribution column.")
    return True


def forwards(apps, schema_editor):
    rows = apps.get_model("notes", "Note")._base_manager.using(schema_editor.connection.alias)
    rows.order_by().update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "note",
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
