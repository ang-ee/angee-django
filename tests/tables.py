"""Table lifecycle for model probes outside Django's native test app setup."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, models


@contextmanager
def model_tables(test_models: tuple[type[models.Model], ...]) -> Iterator[None]:
    """Create and drop only tables that Django's test setup cannot discover.

    Function-scoped models are declared after the session database is created;
    isolated registry probes, unmanaged models, and migrated or uninstalled app labels
    also fall outside syncdb. These tests cannot share one installed composition
    because they exercise alternate or deliberately incomplete model contracts.
    SQLite's schema editor refuses to run inside the rollback transaction, so a
    test using this helper declares ``django_db(transaction=True)``. Static models
    in installed, unmigrated apps need no helper: Django's test database setup
    creates them. This helper never clears existing tables.
    """

    existing = set(connection.introspection.table_names())
    missing = []
    for model in test_models:
        if model._meta.db_table not in existing:
            missing.append(model)
            existing.add(model._meta.db_table)
    if missing:
        with connection.schema_editor() as editor:
            for model in missing:
                editor.create_model(model)
    try:
        yield
    finally:
        if missing:
            with connection.schema_editor() as editor:
                for model in reversed(missing):
                    editor.delete_model(model)


def store_column(model: type[models.Model], pk: object, field_name: str, value: object) -> None:
    """Write ``value`` into one row's column past its field, as an earlier release stored it.

    A test of what a field reads back from storage it would refuse to write now
    (a retired implementation key, say) sets the column with this raw update.
    """

    quote = connection.ops.quote_name
    column = quote(model._meta.get_field(field_name).column)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {quote(model._meta.db_table)} SET {column} = %s WHERE {quote(model._meta.pk.column)} = %s",
            [value, pk],
        )
