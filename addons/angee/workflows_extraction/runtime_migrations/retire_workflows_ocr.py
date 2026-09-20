"""Drop the retired extraction tables only after every historical FK moves."""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

_MOVED = ("extraction", "extractionsource", "extractionpage", "extractionpart")
_OLD_TABLES = {f"workflows_ocr_{name}" for name in _MOVED}


def _target(remote: object) -> tuple[str, str] | None:
    if isinstance(remote, tuple) and len(remote) == 2:
        return str(remote[0]).lower(), str(remote[1]).lower()
    if isinstance(remote, str) and "." in remote:
        app_label, model_name = remote.lower().split(".", 1)
        return app_label, model_name
    return None


def applies(project_state: ProjectState) -> bool:
    """Apply only after current state is complete and no consumer targets old models."""

    old = {("workflows_ocr", name) for name in _MOVED}
    current = {("workflows_extraction", name) for name in _MOVED}
    current.add(("workflows_extraction", "extractionlineage"))
    models = set(project_state.models)
    old_present = old & models
    current_present = current & models
    if not old_present:
        if current_present and current_present != current:
            raise ImproperlyConfigured(
                "angee.workflows_extraction:retire_workflows_ocr found partial current state"
            )
        return False
    if old_present == old and not current_present:
        return False
    if old_present != old or current_present != current:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:retire_workflows_ocr found partial old/current state"
        )
    for key, model in project_state.models.items():
        if key in old:
            continue
        for field in model.fields.values():
            remote = getattr(field, "remote_field", None)
            if remote is not None and _target(remote.model) in old:
                return False
    return True


def drop_retired_tables(apps: Any, schema_editor: Any) -> None:
    """Delete one complete legacy table set after state and database isolation."""

    connection = schema_editor.connection
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    present = _OLD_TABLES & tables
    if not present:
        return
    if present != _OLD_TABLES:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:retire_workflows_ocr found partial legacy tables: "
            + repr(sorted(present))
        )
    external_constraints: list[str] = []
    with connection.cursor() as cursor:
        for table in sorted(tables - _OLD_TABLES):
            constraints = connection.introspection.get_constraints(cursor, table)
            for name, constraint in sorted(constraints.items()):
                foreign_key = constraint.get("foreign_key")
                if foreign_key and foreign_key[0] in _OLD_TABLES:
                    external_constraints.append(
                        f"{table}.{name}->{foreign_key[0]}.{foreign_key[1]}"
                    )
    if external_constraints:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:retire_workflows_ocr found external database "
            "constraints targeting retained tables: " + repr(external_constraints)
        )
    for model_name in ("ExtractionPage", "ExtractionPart", "ExtractionSource", "Extraction"):
        schema_editor.delete_model(apps.get_model("workflows_ocr", model_name))


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("workflows_extraction", "__latest__"),
        ("workflows_ocr", "__latest__"),
    ]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(drop_retired_tables)],
            state_operations=[
                migrations.DeleteModel(name="ExtractionPage"),
                migrations.DeleteModel(name="ExtractionPart"),
                migrations.DeleteModel(name="ExtractionSource"),
                migrations.DeleteModel(name="Extraction"),
            ],
        )
    ]
