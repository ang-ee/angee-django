"""Materialize the existing folder tree without changing parent identities."""

from graphlib import CycleError, TopologicalSorter

from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    model = state.models.get(("storage", "folder"))
    if model is None or "parent" not in model.fields or "drive" not in model.fields:
        # Not this upgrade's floor: an older Folder shape or a smart-folder-only schema.
        return False
    return "path" not in model.fields


def forwards(apps, schema_editor):
    model = apps.get_model("storage", "Folder")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, model):
        return
    rows = model._base_manager.using(alias)
    nodes = {pk: (parent, scope) for pk, parent, scope in rows.order_by("pk").values_list(
        "pk", "parent_id", "drive_id",
    )}
    invalid = [pk for pk, (parent, scope) in nodes.items()
               if parent is not None and (parent not in nodes or nodes[parent][1] != scope)]
    if invalid:
        raise ValueError(f"Folder parents are missing or cross their drive boundary: {invalid}")
    try:
        order = tuple(TopologicalSorter({
            pk: (parent,) if parent is not None else () for pk, (parent, _scope) in nodes.items()
        }).static_order())
    except CycleError as error:
        raise ValueError("Folder parents contain a cycle; repair it before upgrading.") from error
    paths = {}
    for pk in order:
        parent = nodes[pk][0]
        path = (paths[parent] if parent is not None else "/") + f"{pk:012d}/"
        if len(path) > 255:
            raise ValueError(f"Folder {pk} exceeds the supported hierarchy depth.")
        paths[pk] = path
    for pk, path in paths.items():
        rows.filter(pk=pk).update(path=path)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField("folder", "path", models.CharField(default="", editable=False, max_length=255)),
        migrations.RunPython(forwards, migrations.RunPython.noop, hints={"model_name": "folder"}),
    ]
