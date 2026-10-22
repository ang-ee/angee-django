"""Retire the old projection; the following schema migration recreates the ledger.

Events contain no owner data. The old key, public-id target, parent and needs
columns cannot express the new target/record identity contract. Retire that
derived table explicitly, then run ``timeline_backfill`` after the composed
schema migrations. No source history, decisions, tasks or messages are removed.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    event = project_state.models.get(("timeline", "event"))
    return event is not None and "key" in event.fields


class Migration(migrations.Migration):
    operations = [migrations.DeleteModel("Event")]
