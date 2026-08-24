"""Shared pytest infrastructure for framework-core tests."""

from __future__ import annotations

from django.db import connection, models


def _create_missing_tables(
    test_models: tuple[type[models.Model], ...],
) -> list[type[models.Model]]:
    """Create concrete core-test model tables that Django did not synchronize."""

    existing_tables = set(connection.introspection.table_names())
    missing = []
    for model in test_models:
        if model._meta.db_table in existing_tables:
            continue
        missing.append(model)
        existing_tables.add(model._meta.db_table)
    if not missing:
        return []
    with connection.schema_editor() as schema_editor:
        for model in missing:
            schema_editor.create_model(model)
    return missing


def _clear_model_tables(test_models: tuple[type[models.Model], ...]) -> None:
    """Clear schema-editor-created core-test model tables between tests."""

    existing_tables = set(connection.introspection.table_names())
    table_names = []
    for model in test_models:
        table_name = model._meta.db_table
        if table_name not in existing_tables:
            continue
        table_names.append(table_name)
        for field in model._meta.many_to_many:
            through_table_name = field.remote_field.through._meta.db_table
            if through_table_name in existing_tables:
                table_names.append(through_table_name)
    if not table_names:
        return
    with connection.constraint_checks_disabled(), connection.cursor() as cursor:
        for table_name in reversed(tuple(dict.fromkeys(table_names))):
            cursor.execute(f"DELETE FROM {connection.ops.quote_name(table_name)}")
