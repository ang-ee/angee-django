"""A need retires ``original_task``, the task it pointed at before a task merge.

A merge retargeted the need and kept the prior task on this column. The need's
native history records that change now, so the stored pointers leave with the column.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    need = project_state.models.get(("intake", "need"))
    return need is not None and "original_task" in need.fields


class Migration(migrations.Migration):
    operations = [
        migrations.RemoveField("need", "original_task"),
    ]
