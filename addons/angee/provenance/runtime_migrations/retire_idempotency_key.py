"""A changeset retires ``idempotency_key``; its ``scope``/``key`` claim replaces it.

The key only let a retried write replay the changeset it made. The scoped claim
carries that now and starts empty for every existing changeset, so the stored keys
leave with their column.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    changeset = project_state.models.get(("provenance", "changeset"))
    return changeset is not None and "idempotency_key" in changeset.fields


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveField("changeset", "idempotency_key"),
    ]
