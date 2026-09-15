"""Record explicit integration-sync workflow run provenance."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

LEGACY_CHOICES = (
    ("unknown", "Unknown"),
    ("manual", "Manual"),
    ("trigger", "Trigger"),
    ("session", "Session"),
    ("error_workflow", "Error workflow"),
)
SOURCE_PREDECESSOR_CHOICES = (
    ("unknown", "Unknown"),
    ("manual", "Manual"),
    ("test", "Test"),
    ("trigger", "Trigger"),
    ("session", "Session"),
    ("workflow", "Workflow"),
    ("error_workflow", "Error workflow"),
    ("recovery", "Recovery"),
)
CURRENT_CHOICES = (
    ("unknown", "Unknown"),
    ("manual", "Manual"),
    ("test", "Test"),
    ("trigger", "Trigger"),
    ("session", "Session"),
    ("workflow", "Workflow"),
    ("error_workflow", "Error workflow"),
    ("recovery", "Recovery"),
    ("integration_sync", "Integration sync"),
)
LEGACY_VALUES = frozenset(value for value, _label in LEGACY_CHOICES)
SOURCE_PREDECESSOR_VALUES = frozenset(value for value, _label in SOURCE_PREDECESSOR_CHOICES)
CURRENT_VALUES = frozenset(value for value, _label in CURRENT_CHOICES)


def _validate_field_shape(field: object) -> None:
    """Require the canonical run-origin state column invariants."""

    if not isinstance(field, StateField):
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_origin found origin with an incompatible field type"
        )
    if (
        field.default != "unknown"
        or field.db_index is not True
        or field.null is not False
        or field.blank is not False
    ):
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_origin found origin with an incompatible field shape"
        )


def applies(project_state: ProjectState) -> bool:
    """Apply only to the complete historical run-origin declaration."""

    run = project_state.models.get(("workflows", "workflowrun"))
    if run is None:
        return False
    field = run.fields.get("origin")
    if field is None:
        raise ImproperlyConfigured(
            "angee.workflows:workflow_integration_sync_origin found WorkflowRun without origin"
        )
    _validate_field_shape(field)
    values = frozenset(value for value, _label in field.choices)
    width = field.max_length
    if CURRENT_VALUES <= values and isinstance(width, int) and width >= 16:
        return False
    if values in (LEGACY_VALUES, SOURCE_PREDECESSOR_VALUES) and width == 14:
        return True
    raise ImproperlyConfigured(
        "angee.workflows:workflow_integration_sync_origin found a partial origin transition"
    )


class Migration(migrations.Migration):
    """Widen WorkflowRun.origin and record all current provenance values."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField(
            model_name="workflowrun",
            name="origin",
            field=StateField(
                choices=CURRENT_CHOICES,
                db_index=True,
                default="unknown",
                max_length=16,
            ),
        ),
    ]
