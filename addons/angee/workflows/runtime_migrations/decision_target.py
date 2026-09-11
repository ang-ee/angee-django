"""Add optional immutable related-record identity to workflow decisions."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply only to a Decision shape with neither target field installed."""

    decision = project_state.models.get(("workflows", "decision"))
    if decision is None:
        return False
    target_fields = {"target_model", "target_id", "target_tab"}
    present = {field for field in target_fields if field in decision.fields}
    if not present:
        return True
    if present == target_fields:
        return False
    raise ImproperlyConfigured("angee.workflows:decision_target found a partial Decision target pair")


class Migration(migrations.Migration):
    """Persist the optional public record reference used to scope review UI."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="decision",
            name="target_model",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="decision",
            name="target_tab",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="decision",
            name="target_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddIndex(
            model_name="decision",
            index=models.Index(fields=("target_model", "target_id"), name="idx_wdc_target"),
        ),
        migrations.AddConstraint(
            model_name="decision",
            constraint=models.CheckConstraint(
                condition=(models.Q(target_model="", target_id="", target_tab="")
                           | (~models.Q(target_model="") & ~models.Q(target_id=""))),
                name="chk_wdc_target_pair",
            ),
        ),
    ]
