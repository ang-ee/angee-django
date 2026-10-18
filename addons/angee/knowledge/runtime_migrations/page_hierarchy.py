"""Materialize the existing page tree without changing parent identities."""

from graphlib import CycleError, TopologicalSorter

from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    model = state.models.get(("knowledge", "page"))
    if model is None:
        return False
    if "parent" not in model.fields or "vault" not in model.fields:
        raise ValueError("The Page hierarchy requires its existing parent and vault fields.")
    return "path" not in model.fields


def forwards(apps, schema_editor):
    model = apps.get_model("knowledge", "Page")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, model):
        return
    rows = model._base_manager.using(alias)
    nodes = {pk: (parent, scope) for pk, parent, scope in rows.order_by("pk").values_list(
        "pk", "parent_id", "vault_id",
    )}
    invalid = [pk for pk, (parent, scope) in nodes.items()
               if parent is not None and (parent not in nodes or nodes[parent][1] != scope)]
    if invalid:
        raise ValueError(f"Page parents are missing or cross their vault boundary: {invalid}")
    try:
        order = tuple(TopologicalSorter({
            pk: (parent,) if parent is not None else () for pk, (parent, _scope) in nodes.items()
        }).static_order())
    except CycleError as error:
        raise ValueError("Page parents contain a cycle; repair it before upgrading.") from error
    paths = {}
    for pk in order:
        parent = nodes[pk][0]
        path = (paths[parent] if parent is not None else "/") + f"{pk:012d}/"
        if len(path) > 255:
            raise ValueError(f"Page {pk} exceeds the supported hierarchy depth.")
        paths[pk] = path
    for pk, path in paths.items():
        rows.filter(pk=pk).update(path=path)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField("page", "path", models.CharField(default="", editable=False, max_length=255)),
        migrations.RunPython(forwards, migrations.RunPython.noop, hints={"model_name": "page"}),
    ]
