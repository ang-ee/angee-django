"""A dashboard retires its creation-key columns; changeset claims own create idempotency.

``client_creation_key`` and ``creation_fingerprint`` only let a retried create replay
the dashboard it made. A changeset claim carries that now, so the stored keys leave
with their columns and the owner-scoped uniqueness constraint. The cutover is explicit
because a populated column must never be dropped by autodetection.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    dashboard = project_state.models.get(("dashboards", "dashboard"))
    return dashboard is not None and "client_creation_key" in dashboard.fields and any(
        constraint.name == "dashboard_owner_creation_key" for constraint in dashboard.options.get("constraints", ())
    )


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveConstraint("dashboard", "dashboard_owner_creation_key"),
        migrations.RemoveField("dashboard", "client_creation_key"),
        migrations.RemoveField("dashboard", "creation_fingerprint"),
    ]
