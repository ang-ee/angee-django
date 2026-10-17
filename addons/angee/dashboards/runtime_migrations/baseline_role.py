"""Freeze former ownerless baselines before ownership can be cleared or transferred."""

from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    dashboard = state.models.get(("dashboards", "dashboard"))
    return dashboard is not None and "is_baseline" not in dashboard.fields


def forwards(apps, schema_editor):
    model = apps.get_model("dashboards", "Dashboard")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, model):
        return
    rows = model._base_manager.using(alias)
    rows.filter(owner__isnull=True, scope__in=("addon", "resource")).update(is_baseline=True)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField("dashboard", "is_baseline", models.BooleanField(default=False, editable=False)),
        migrations.RunPython(forwards, migrations.RunPython.noop, hints={"model_name": "dashboard"}),
        migrations.RemoveConstraint("dashboard", "dashboard_installed_scoped_target"),
        migrations.AddConstraint(
            "dashboard", models.UniqueConstraint(
                fields=("scope", "scope_key"), condition=models.Q(is_baseline=True),
                name="dashboard_installed_scoped_target",
            ),
        ),
        migrations.AddConstraint(
            "dashboard", models.CheckConstraint(
                condition=models.Q(is_baseline=False) | models.Q(owner__isnull=True, scope__in=("addon", "resource")),
                name="dashboard_baseline_shape",
            ),
        ),
    ]
