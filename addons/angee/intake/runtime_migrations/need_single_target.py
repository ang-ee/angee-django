"""Remove copied task-project context before enforcing one stored target."""

from django.db import migrations, models
from django.db.migrations.state import ProjectState

CONSTRAINT_NAME = "ck_intake_need_exactly_one_target"
LEGACY_CONSTRAINT = models.CheckConstraint(
    condition=models.Q(targets_project=False, task__isnull=False)
    | models.Q(targets_project=True, task__isnull=True, project__isnull=False),
    name=CONSTRAINT_NAME,
)
TARGET_CONSTRAINT = models.CheckConstraint(
    condition=models.Q(task__isnull=False, project__isnull=True)
    | models.Q(task__isnull=True, project__isnull=False),
    name=CONSTRAINT_NAME,
)


def applies(project_state: ProjectState) -> bool:
    need = project_state.models.get(("intake", "need"))
    if need is None:
        return False
    constraints = need.options.get("constraints", [])
    if "targets_project" not in need.fields:
        if TARGET_CONSTRAINT not in constraints:
            raise ValueError("Need has a partial single-target transition; reconcile its migration history.")
        return False
    if LEGACY_CONSTRAINT not in constraints:
        raise ValueError("Need's legacy target constraint is missing or changed; reconcile its migration history.")
    return True


def immediate_constraints(schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def forwards(apps, schema_editor):
    immediate_constraints(schema_editor)
    rows = apps.get_model("intake", "Need")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(targets_project=False).update(project_id=None)


def backwards(apps, schema_editor):
    immediate_constraints(schema_editor)
    rows = apps.get_model("intake", "Need")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(task__isnull=True).update(targets_project=True)
    tasks = apps.get_model("projects", "Task")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(task__isnull=False).update(
        project_id=models.Subquery(tasks.filter(pk=models.OuterRef("task_id")).values("project_id")[:1]),
        targets_project=False,
    )


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint("need", CONSTRAINT_NAME),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField("need", "targets_project"),
        migrations.AddConstraint("need", TARGET_CONSTRAINT),
    ]
