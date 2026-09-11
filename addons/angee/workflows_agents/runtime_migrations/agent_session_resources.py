"""Order new agent-session backfills after the resources-owned install ledger."""

from __future__ import annotations

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Wait for both owners before adding the cross-app ordering dependency."""

    return all(
        key in project_state.models
        for key in (("workflows", "workflow"), ("resources", "resource"))
    )


class Migration(migrations.Migration):
    """Preserve the original backfill source while ordering new materializations.

    The manifest declares this before agent_session_identity. The composer chains
    each declaration onto the preceding workflow leaf, so a new backfill inherits
    the resource dependency. An already materialized backfill remains unchanged.
    """

    dependencies = [("resources", "__latest__")]
    operations = []
