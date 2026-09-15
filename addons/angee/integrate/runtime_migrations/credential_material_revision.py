"""Add the monotonic generation for canonical credential material."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Add the field only to a concrete Credential that does not yet own it."""

    model = project_state.models.get(("integrate", "credential"))
    if model is None:
        return False
    field = model.fields.get("material_revision")
    if field is None:
        return True
    if (
        isinstance(field, models.PositiveBigIntegerField)
        and field.default == 1
        and field.editable is False
        and field.null is False
    ):
        return False
    raise ImproperlyConfigured(
        "angee.integrate:credential_material_revision found a partial Credential material revision transition"
    )


class Migration(migrations.Migration):
    """Backfill existing credentials to their first known material generation."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="credential",
            name="material_revision",
            field=models.PositiveBigIntegerField(default=1, editable=False),
        ),
    ]
