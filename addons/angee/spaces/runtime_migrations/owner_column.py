"""Preserve existing group ownership while separating it from attribution."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    group = project_state.models.get(("spaces", "group"))
    return group is not None and "created_by" in group.fields and "owner" not in group.fields


def forwards(apps, schema_editor):
    group = apps.get_model("spaces", "Group")
    group._base_manager.using(schema_editor.connection.alias).order_by().filter(
        owner__isnull=True,
    ).update(owner_id=models.F("created_by_id"))


def backwards(apps, schema_editor):
    group = apps.get_model("spaces", "Group")
    unchanged = models.Q(owner__isnull=True, created_by__isnull=True) | models.Q(
        owner__isnull=False, created_by__isnull=False, owner_id=models.F("created_by_id"),
    )
    if group._base_manager.using(schema_editor.connection.alias).order_by().exclude(unchanged).exists():
        raise IrreversibleError("Transferred or cleared group ownership cannot revert to creator-based access.")


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "group",
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
        migrations.RunPython(forwards, backwards),
    ]
