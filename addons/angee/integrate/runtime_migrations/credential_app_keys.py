"""Add the vendor-neutral application-key credential kind."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

LEGACY_CHOICES = (
    ("oauth", "OAuth"),
    ("static_token", "Static Token"),
    ("ssh_key", "SSH Key"),
    ("basic_auth", "Basic Auth"),
)
CURRENT_CHOICES = (*LEGACY_CHOICES, ("app_keys", "App Keys"))
LEGACY_VALUES = frozenset(value for value, _label in LEGACY_CHOICES)
CURRENT_VALUES = frozenset(value for value, _label in CURRENT_CHOICES)


def applies(project_state: ProjectState) -> bool:
    """Return whether Credential has exactly the legacy kind vocabulary."""

    model = project_state.models.get(("integrate", "credential"))
    if model is None:
        return False
    field = model.fields.get("kind")
    if field is None:
        raise ImproperlyConfigured("angee.integrate:credential_app_keys found Credential without kind")
    values = frozenset(value for value, _label in field.choices)
    if CURRENT_VALUES <= values:
        return False
    if values == LEGACY_VALUES:
        return True
    raise ImproperlyConfigured(
        f"angee.integrate:credential_app_keys found a partial Credential kind transition: {sorted(values)}"
    )


class Migration(migrations.Migration):
    """Record app_keys in Credential.kind's StateField choices."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField(
            model_name="credential",
            name="kind",
            field=StateField(
                choices=CURRENT_CHOICES,
                max_length=12,
            ),
        ),
    ]
