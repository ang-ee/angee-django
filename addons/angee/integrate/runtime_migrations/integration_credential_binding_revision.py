"""Add the monotonic credential-binding generation to Integration."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Add the field only to a concrete Integration that does not own it yet."""

    model = project_state.models.get(("integrate", "integration"))
    if model is None:
        return False
    field = model.fields.get("credential_binding_revision")
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
        "angee.integrate:integration_credential_binding_revision found a partial "
        "Integration credential-binding revision transition"
    )


class Migration(migrations.Migration):
    """Backfill existing integrations to their first known credential binding."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="integration",
            name="credential_binding_revision",
            field=models.PositiveBigIntegerField(default=1, editable=False),
        ),
    ]
