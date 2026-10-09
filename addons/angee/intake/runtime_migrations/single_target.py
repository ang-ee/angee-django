"""Derive a released Need's single target from its retired ``targets_project`` flag.

Released histories mark a project request with ``targets_project`` and may also
copy the task's project onto a task request. The current Need targets exactly one
of a task or a project. A flagged request keeps its project; any other keeps its
task, and a copied project is cleared. A row the flag cannot explain stops the
migration. The flag and its constraint then go.
"""

from django.db import migrations


def applies(state):
    need = state.models.get(("intake", "need"))
    return need is not None and "targets_project" in need.fields


def single_target(apps, schema_editor):
    needs = apps.get_model("intake", "Need")._base_manager.using(schema_editor.connection.alias)
    if needs.filter(targets_project=True).exclude(project__isnull=False, task__isnull=True).exists():
        raise ValueError("A project request has no project or also names a task.")
    if needs.filter(targets_project=False, task__isnull=True).exists():
        raise ValueError("A task request names no task.")
    needs.filter(targets_project=False, project__isnull=False).update(project=None)


class Migration(migrations.Migration):
    dependencies = [("intake", "__latest__")]
    operations = [
        migrations.RemoveConstraint("need", "ck_intake_need_exactly_one_target"),
        migrations.RunPython(single_target, migrations.RunPython.noop),
        migrations.RemoveField("need", "targets_project"),
    ]
