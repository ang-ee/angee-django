"""A quota ledger keeps one mode: a backend-declared scope with a required integration.

The dual mode kept legacy per-integration ledgers (``scope`` NULL, never reopened,
bounded by ``quota_limit``) beside scoped ones whose spender could be deleted
(``integration`` NULL). Neither row fits the required ``integration`` and ``scope``,
so both are deleted before the columns tighten; a scope whose current-period row
lost its spender starts that period's count again. ``quota_limit`` leaves with them.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    quota = project_state.models.get(("posts", "quota"))
    return quota is not None and "quota_limit" in quota.fields


def forwards(apps, schema_editor):
    rows = apps.get_model("posts", "Quota")._base_manager.using(schema_editor.connection.alias)
    rows.filter(models.Q(scope__isnull=True) | models.Q(integration__isnull=True)).delete()


class Migration(migrations.Migration):
    dependencies = [("integrate", "__latest__")]
    operations = [
        migrations.RemoveConstraint("quota", "uq_quota_integration_period"),
        migrations.RemoveConstraint("quota", "uq_quota_scope_period"),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterField(
            "quota",
            "integration",
            models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="quotas",
                to="integrate.integration",
            ),
        ),
        migrations.AlterField("quota", "scope", models.CharField(max_length=512)),
        migrations.AddConstraint(
            "quota",
            models.UniqueConstraint(fields=("scope", "period_start"), name="uq_quota_scope_period"),
        ),
        migrations.RemoveField("quota", "quota_limit"),
    ]
