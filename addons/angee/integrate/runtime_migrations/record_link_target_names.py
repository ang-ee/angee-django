"""Preserve generic target references when RecordLink's columns are renamed."""

from django.db import migrations


def applies(project_state):
    model = project_state.models.get(("integrate", "recordlink"))
    if model is None:
        return False
    fields = model.fields
    old = {"target_ct", "target_id"} <= fields.keys()
    new = {"target_content_type", "target_object_id"} <= fields.keys()
    if old and not new:
        return True
    if new and not old:
        return False
    raise ValueError("RecordLink has a partial generic target rename; complete or reverse its migration history.")


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RenameField("recordlink", "target_ct", "target_content_type"),
        migrations.RenameField("recordlink", "target_id", "target_object_id"),
    ]
