"""Preserve existing drive and file access when ownership leaves attribution."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    roots = [project_state.models.get(("storage", name)) for name in ("drive", "file")]
    present = [root for root in roots if root is not None]
    if not present or all("owner" in root.fields for root in present):
        return False
    if len(present) != 2 or any("owner" in root.fields for root in present):
        raise ValueError("Complete or reverse the partial storage ownership transition before upgrading.")
    return True


def forwards(apps, schema_editor):
    for name in ("Drive", "File"):
        rows = apps.get_model("storage", name)._base_manager.using(schema_editor.connection.alias).order_by()
        rows.filter(owner__isnull=True).update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            name,
            "owner",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=audit_set_null,
                related_name="+",
            ),
        )
        for name in ("drive", "file")
    ] + [migrations.RunPython(forwards, migrations.RunPython.noop)]
