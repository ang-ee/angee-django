"""Add declared workflow purpose and truthful run/runtime origins."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

WORKFLOW_PURPOSE_CHOICES = (("automation", "Automation"), ("agent_session", "Agent session"))
RUN_ORIGIN_CHOICES = (
    ("unknown", "Unknown"),
    ("manual", "Manual"),
    ("trigger", "Trigger"),
    ("session", "Session"),
    ("error_workflow", "Error workflow"),
)
WAITING_KIND_CHOICES = (
    ("scheduled", "Scheduled"),
    ("approval", "Approval"),
    ("external", "External input"),
    ("children", "Child steps"),
)

_FIELDS = {
    ("workflows", "workflow"): frozenset({"purpose"}),
    ("workflows", "workflowrun"): frozenset({"origin"}),
    ("workflows", "steprun"): frozenset({"waiting_kind"}),
}


def applies(project_state: ProjectState) -> bool:
    """Apply only to the complete pre-identity workflow runtime shape."""

    models_by_key = {key: project_state.models.get(key) for key in _FIELDS}
    if all(model is None for model in models_by_key.values()):
        return False
    if any(model is None for model in models_by_key.values()):
        raise ImproperlyConfigured("angee.workflows:workflow_identity found a partial workflow runtime")
    present = {
        key: expected.intersection(cast_model.fields)
        for key, expected in _FIELDS.items()
        if (cast_model := models_by_key[key]) is not None
    }
    if all(not fields for fields in present.values()):
        return True
    if all(fields == _FIELDS[key] for key, fields in present.items()):
        return False
    raise ImproperlyConfigured("angee.workflows:workflow_identity found a partial identity transition")


def backfill_structural_run_origins(apps, schema_editor) -> None:
    """Recover only origins proven by persisted engine links."""

    run = apps.get_model("workflows", "WorkflowRun")
    alias = schema_editor.connection.alias
    rows = run._base_manager.using(alias).filter(origin="unknown")
    rows.filter(trigger_id__isnull=False).update(origin="trigger")
    rows.filter(parent_step_run_id__isnull=False).update(origin="error_workflow")


class Migration(migrations.Migration):
    """Persist workflow classification, run caller, and producer-declared waits."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="workflow",
            name="purpose",
            field=StateField(
                choices=WORKFLOW_PURPOSE_CHOICES,
                db_index=True,
                default="automation",
                max_length=13,
            ),
        ),
        migrations.AddField(
            model_name="workflowrun",
            name="origin",
            field=StateField(
                choices=RUN_ORIGIN_CHOICES,
                db_index=True,
                default="unknown",
                max_length=14,
            ),
        ),
        migrations.AddField(
            model_name="steprun",
            name="waiting_kind",
            field=StateField(
                blank=True,
                choices=WAITING_KIND_CHOICES,
                db_index=True,
                default="",
                max_length=9,
            ),
        ),
        migrations.RunPython(backfill_structural_run_origins, migrations.RunPython.noop),
    ]
