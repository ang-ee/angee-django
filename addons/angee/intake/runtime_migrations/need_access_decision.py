"""Retain linked accounts as approved request access without inventing a resolver."""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField
from angee.base.mixins import audit_set_null

FIELDS = {"access_verdict", "access_resolution", "access_resolved_by", "access_resolved_at"}


def applies(project_state: ProjectState) -> bool:
    need = project_state.models.get(("intake", "need"))
    if need is None:
        return False
    present = FIELDS.intersection(need.fields)
    if present and present != FIELDS:
        raise ValueError("Need has a partial access-decision transition; reconcile its migration history.")
    return not present


def forwards(apps, schema_editor):
    rows = apps.get_model("intake", "Need")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(access_verdict="pending", party__person__user__isnull=False).update(
        access_verdict="completed", access_resolution={"action": "approve", "reason": ""},
    )


def backwards(apps, schema_editor):
    rows = apps.get_model("intake", "Need")._base_manager.using(schema_editor.connection.alias).order_by()
    if rows.filter(access_resolved_at__isnull=False).exists():
        raise IrreversibleError("Decided request access cannot be discarded.")


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("parties", "__latest__")]
    operations = [
        migrations.AddField(
            "need", "access_verdict",
            StateField(
                choices=[("pending", "Pending"), ("completed", "Approved"), ("rejected", "Denied")],
                default="pending", editable=False,
            ),
        ),
        migrations.AddField(
            "need", "access_resolved_by",
            models.ForeignKey(
                settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                on_delete=audit_set_null, related_name="+",
            ),
        ),
        migrations.AddField(
            "need", "access_resolved_at", models.DateTimeField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            "need", "access_resolution", models.JSONField(default=dict, blank=True, editable=False),
        ),
        migrations.RunPython(forwards, backwards),
    ]
