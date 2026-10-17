"""Preserve removed personal dashboards under the shared trash contract.

Legacy archive timestamps are unknown and remain NULL. Widget is_archived is
snapshot availability and deliberately remains unchanged.
"""

from django.conf import settings
from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    dashboard = state.models.get(("dashboards", "dashboard"))
    if dashboard is None or "is_archived" not in dashboard.fields:
        return False
    if any(name in dashboard.fields for name in ("is_trashed", "trashed_at", "trashed_by", "trash_reason")):
        raise ValueError("Complete or reverse the partial dashboard trash transition before upgrading.")
    return True


def forwards(apps, schema_editor):
    model = apps.get_model("dashboards", "Dashboard")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, model):
        return
    rows = model._base_manager.using(alias)
    rows.filter(is_trashed=True).update(trash_reason="Migrated from the former dashboard archive.")


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RemoveConstraint("dashboard", "dashboard_scoped_not_archived"),
        migrations.RenameField("dashboard", "is_archived", "is_trashed"),
        migrations.AlterField(
            "dashboard", "is_trashed", models.BooleanField(default=False, db_index=True, editable=False),
        ),
        migrations.AddField(
            "dashboard", "trashed_at", models.DateTimeField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            "dashboard", "trashed_by", models.ForeignKey(
                settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                related_name="+", editable=False,
            ),
        ),
        migrations.AddField("dashboard", "trash_reason", models.TextField(blank=True, default="", editable=False)),
        migrations.RunPython(forwards, migrations.RunPython.noop, hints={"model_name": "dashboard"}),
        migrations.AddConstraint(
            "dashboard", models.CheckConstraint(
                condition=models.Q(scope="personal") | models.Q(is_trashed=False),
                name="dashboard_scoped_not_trashed",
            ),
        ),
    ]
