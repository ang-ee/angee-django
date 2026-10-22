"""A vault retires its creation-key columns; changeset claims own create idempotency.

``client_creation_key`` and ``creation_fingerprint`` only let a retried create replay
the vault it made, and the history rows copied them. A changeset claim carries that
now, so the stored keys leave with their columns, in the vault and its history, and
with the creator-scoped uniqueness constraint.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    vault = project_state.models.get(("knowledge", "vault"))
    history = project_state.models.get(("knowledge", "historicalvault"))
    return (
        vault is not None
        and history is not None
        and "client_creation_key" in vault.fields
        and "client_creation_key" in history.fields
        and any(constraint.name == "knowledge_vault_creation_key" for constraint in vault.options.get("constraints", ()))
    )


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveConstraint("vault", "knowledge_vault_creation_key"),
        migrations.RemoveField("vault", "client_creation_key"),
        migrations.RemoveField("vault", "creation_fingerprint"),
        migrations.RemoveField("historicalvault", "client_creation_key"),
        migrations.RemoveField("historicalvault", "creation_fingerprint"),
    ]
