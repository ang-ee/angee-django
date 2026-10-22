"""A clarification task retires its creation-key columns; changeset claims own replay.

``clarification_creation_key`` and ``clarification_creation_fingerprint`` only let a
retried question replay the task it made, and task history copied them. A changeset
claim carries that now, so the stored keys leave with their columns and the
round-and-asker uniqueness constraint.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    task = project_state.models.get(("projects", "task"))
    history = project_state.models.get(("projects", "historicaltask"))
    return (
        task is not None
        and history is not None
        and "clarification_creation_key" in task.fields
        and "clarification_creation_key" in history.fields
        and any(
            constraint.name == "uq_proposals_clarification_creation_key"
            for constraint in task.options.get("constraints", ())
        )
    )


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveConstraint("task", "uq_proposals_clarification_creation_key"),
        migrations.RemoveField("task", "clarification_creation_key"),
        migrations.RemoveField("task", "clarification_creation_fingerprint"),
        migrations.RemoveField("historicaltask", "clarification_creation_key"),
        migrations.RemoveField("historicaltask", "clarification_creation_fingerprint"),
    ]
