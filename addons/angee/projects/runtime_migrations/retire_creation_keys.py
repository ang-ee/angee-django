"""Tasks and milestones retire their creation-key columns, and setup receipts leave.

``client_creation_key`` and ``creation_fingerprint`` only let a retried create replay
the task or milestone it made, and task history copied them. ``ProjectSetupReceipt``
held the same replay identity for project setup. Changeset claims carry it now, so
the stored keys leave with their columns and constraints, and the receipts with
their table.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def _constrained(model, name: str) -> bool:
    return any(constraint.name == name for constraint in model.options.get("constraints", ()))


def applies(project_state: ProjectState) -> bool:
    task = project_state.models.get(("projects", "task"))
    history = project_state.models.get(("projects", "historicaltask"))
    milestone = project_state.models.get(("projects", "milestone"))
    return (
        task is not None
        and history is not None
        and milestone is not None
        and ("projects", "projectsetupreceipt") in project_state.models
        and all("client_creation_key" in model.fields for model in (task, history, milestone))
        and _constrained(task, "projects_task_creation_key")
        and _constrained(milestone, "projects_milestone_creation_key")
    )


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveConstraint("task", "projects_task_creation_key"),
        migrations.RemoveField("task", "client_creation_key"),
        migrations.RemoveField("task", "creation_fingerprint"),
        migrations.RemoveField("historicaltask", "client_creation_key"),
        migrations.RemoveField("historicaltask", "creation_fingerprint"),
        migrations.RemoveConstraint("milestone", "projects_milestone_creation_key"),
        migrations.RemoveField("milestone", "client_creation_key"),
        migrations.RemoveField("milestone", "creation_fingerprint"),
        migrations.DeleteModel("ProjectSetupReceipt"),
    ]
