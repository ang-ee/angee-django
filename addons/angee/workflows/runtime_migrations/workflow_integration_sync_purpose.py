"""Add the integration-sync workflow purpose and widen its state column."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

LEGACY_CHOICES = (("automation", "Automation"), ("agent_session", "Agent session"))
CURRENT_CHOICES = (*LEGACY_CHOICES, ("integration_sync", "Integration sync"))
LEGACY_VALUES = frozenset(value for value, _label in LEGACY_CHOICES)
CURRENT_VALUES = frozenset(value for value, _label in CURRENT_CHOICES)


def _validate_field_shape(field: object) -> None:
    """Reject malformed states before deciding whether this migration applies."""

    if not isinstance(field, StateField):
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_purpose found purpose with "
            "an incompatible field type"
        )
    if (
        field.default != "automation"
        or field.db_index is not True
        or field.null is not False
        or field.blank is not False
    ):
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_purpose found purpose with "
            "an incompatible field shape"
        )


def applies(project_state: ProjectState) -> bool:
    """Apply only when Workflow.purpose has the exact prior choice set."""

    workflow = project_state.models.get(("workflows", "workflow"))
    if workflow is None:
        return False
    field = workflow.fields.get("purpose")
    if field is None:
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_purpose found Workflow without purpose"
        )
    _validate_field_shape(field)
    values = frozenset(value for value, _label in field.choices)
    width = field.max_length
    if CURRENT_VALUES <= values and isinstance(width, int) and width >= 16:
        return False
    if values == LEGACY_VALUES and width == 13:
        return True
    raise ImproperlyConfigured(
        "angee.workflows:workflow_integration_sync_purpose found a partial purpose transition"
    )


class Migration(migrations.Migration):
    """Record integration_sync in Workflow.purpose and widen its column."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField(
            model_name="workflow",
            name="purpose",
            field=StateField(
                choices=CURRENT_CHOICES,
                db_index=True,
                default="automation",
                max_length=16,
            ),
        ),
    ]
