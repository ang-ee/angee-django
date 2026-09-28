"""Separate project and task ownership from their retained attribution.

Reversal drops owner columns and loses post-upgrade transfers. Export ownership
before a downgrade when those changes must survive; attribution is not rewritten.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    names = ("project", "task")
    present = [project_state.models.get(("projects", name)) for name in names]
    if all(model is None for model in present):
        return False
    if any(model is None for model in present):
        raise ValueError("Project ownership requires both existing project and task models.")
    owners = ["owner" in model.fields for model in present]
    if all(owners):
        return False
    if any(owners) or any("created_by" not in model.fields for model in present):
        raise ValueError("Complete or reverse the partial projects ownership transition before upgrading.")
    return True


def forwards(apps, schema_editor):
    for name in ("Project", "Task"):
        rows = apps.get_model("projects", name)._base_manager.using(schema_editor.connection.alias).order_by()
        rows.filter(owner__isnull=True).update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            name, "owner",
            models.ForeignKey(
                settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                on_delete=audit_set_null, related_name="+",
            ),
        )
        for name in ("project", "task")
    ] + [migrations.RunPython(forwards, migrations.RunPython.noop)]
