"""Retire the derived Event table; source rows now supply the UNION ALL read.

No owner history, decisions, tasks, workflows or messages are removed. Keep the
previous declared migration immutable for hosts that already materialized it.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    return ("timeline", "event") in project_state.models


class Migration(migrations.Migration):
    operations = [migrations.DeleteModel("Event")]
