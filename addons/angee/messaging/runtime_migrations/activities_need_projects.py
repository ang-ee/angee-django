"""Retire messaging's activities on a stack that does not compose projects.

Tasks about a record now live in ``angee.projects``; where projects composes,
its ``card_tasks`` migration carries every activity forward and retires these
models itself. A stack without projects has nowhere to carry them: it refuses
while activities exist, and drops the empty tables otherwise.
"""

from django.db import migrations


def applies(state):
    return ("messaging", "threadactivity") in state.models and ("projects", "task") not in state.models


def refuse_live_activities(apps, schema_editor):
    rows = apps.get_model("messaging", "ThreadActivity")._base_manager.using(schema_editor.connection.alias)
    count = rows.count()
    if count:
        raise ValueError(
            f"{count} record activities need angee.projects, which now owns tasks about records; "
            "compose it before upgrading."
        )


class Migration(migrations.Migration):
    dependencies = [("messaging", "__latest__")]
    operations = [
        migrations.RunPython(refuse_live_activities, migrations.RunPython.noop),
        migrations.DeleteModel("ThreadActivity"),
        migrations.DeleteModel("ActivityType"),
    ]
