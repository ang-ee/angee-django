"""A message retires its creation-key columns; changeset claims own create idempotency.

``client_creation_key``, ``creation_fingerprint`` and the ``creation_actor`` that
scoped them only let a retried post replay the message it made. A changeset claim
carries that now, so the stored keys leave with their columns and the actor-scoped
uniqueness constraint.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    message = project_state.models.get(("messaging", "message"))
    return message is not None and "client_creation_key" in message.fields and any(
        constraint.name == "messaging_message_creation_key" for constraint in message.options.get("constraints", ())
    )


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveConstraint("message", "messaging_message_creation_key"),
        migrations.RemoveField("message", "client_creation_key"),
        migrations.RemoveField("message", "creation_actor"),
        migrations.RemoveField("message", "creation_fingerprint"),
    ]
