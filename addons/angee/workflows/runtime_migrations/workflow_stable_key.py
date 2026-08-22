"""Add the resource-assignable stable key of a workflow lineage."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

_CONSTRAINT_NAME = "uniq_workflows_workflow_head_key"


def applies(project_state: ProjectState) -> bool:
    """Return whether Workflow has its lineage link but not its stable key."""

    model = project_state.models.get(("workflows", "workflow"))
    if model is None:
        return False
    fields = frozenset(model.fields)
    constraints = tuple(model.options.get("constraints", ()))
    has_constraint = any(getattr(constraint, "name", None) == _CONSTRAINT_NAME for constraint in constraints)
    if "published_from" not in fields:
        raise ImproperlyConfigured(
            "angee.workflows:workflow_stable_key found Workflow without its lineage head link"
        )
    if "key" not in fields and not has_constraint:
        return True
    if "key" in fields and has_constraint:
        return False
    raise ImproperlyConfigured(
        "angee.workflows:workflow_stable_key found a partial Workflow stable key transition"
    )


class Migration(migrations.Migration):
    """Add a stable key unique across assigned workflow lineage heads."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="workflow",
            name="key",
            field=models.SlugField(blank=True, default="", max_length=100),
        ),
        migrations.AddConstraint(
            model_name="workflow",
            constraint=models.UniqueConstraint(
                fields=("key",),
                condition=models.Q(published_from__isnull=True) & ~models.Q(key=""),
                name=_CONSTRAINT_NAME,
                violation_error_code="unique",
                violation_error_message="A workflow with this key already exists.",
            ),
        ),
    ]
