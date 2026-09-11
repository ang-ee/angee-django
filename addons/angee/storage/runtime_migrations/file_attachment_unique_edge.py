"""Reconcile duplicate file edges before enforcing canonical uniqueness."""

from __future__ import annotations

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("storage", "fileattachment"))
    if model is None:
        return False
    return not any(
        getattr(item, "name", "") == "uq_storage_file_attachment_edge"
        for item in model.options.get("constraints", ())
    )


def reconcile_duplicate_edges(apps, schema_editor) -> None:
    attachment_model = apps.get_model("storage", "FileAttachment")
    rows = attachment_model._base_manager.using(schema_editor.connection.alias).order_by(
        "file_id", "content_type_id", "object_id", "pk"
    )
    current_key = None
    keeper = None
    for row in rows.iterator(chunk_size=1_000):
        key = (row.file_id, row.content_type_id, row.object_id)
        if key != current_key:
            current_key = key
            keeper = row
            continue
        if keeper is not None and not keeper.label and row.label:
            type(row)._base_manager.using(schema_editor.connection.alias).filter(pk=keeper.pk).update(
                label=row.label
            )
            keeper.label = row.label
        type(row)._base_manager.using(schema_editor.connection.alias).filter(pk=row.pk).delete()


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(reconcile_duplicate_edges, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="fileattachment",
            constraint=models.UniqueConstraint(
                fields=("file", "content_type", "object_id"),
                name="uq_storage_file_attachment_edge",
            ),
        ),
    ]
