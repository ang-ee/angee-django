"""Materialize task ancestry without changing parents or project membership."""

from graphlib import CycleError, TopologicalSorter

from django.db import migrations, models, router


def applies(state):
    model = state.models.get(("projects", "task"))
    return model is not None and "parent" in model.fields and "path" not in model.fields


def forwards(apps, schema_editor):
    model = apps.get_model("projects", "Task")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, model):
        return
    rows = model._base_manager.using(alias)
    nodes = dict(rows.order_by("pk").values_list("pk", "parent_id"))
    missing = [pk for pk, parent in nodes.items() if parent is not None and parent not in nodes]
    if missing:
        raise ValueError(f"Task parents are missing: {missing}")
    try:
        order = tuple(TopologicalSorter({pk: (parent,) if parent is not None else ()
                                        for pk, parent in nodes.items()}).static_order())
    except CycleError as error:
        raise ValueError("Task parents contain a cycle; repair it before upgrading.") from error
    paths = {}
    for pk in order:
        parent = nodes[pk]
        path = (paths[parent] if parent is not None else "/") + f"{pk:012d}/"
        if len(path) > 255:
            raise ValueError(f"Task {pk} exceeds the supported hierarchy depth.")
        paths[pk] = path
    for pk, path in paths.items():
        rows.filter(pk=pk).update(path=path)


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddField("task", "path", models.CharField(default="", editable=False, max_length=255)),
        migrations.RunPython(forwards, migrations.RunPython.noop, hints={"model_name": "task"}),
    ]
