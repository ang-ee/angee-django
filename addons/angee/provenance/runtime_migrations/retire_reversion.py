"""Retire django-reversion after it leaves the composed app set.

All Version and Revision data is gone by ruling; it is not converted to native
history. Reverse is a no-op and cannot restore that data or migration records.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    return not any(label == "reversion" for label, _ in state.models)


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    connection = schema_editor.connection
    tables = connection.introspection.table_names()
    for table in ("reversion_version", "reversion_revision"):
        if table in tables:
            schema_editor.execute(f"DROP TABLE {schema_editor.quote_name(table)}")
    recorder = MigrationRecorder(connection)
    if recorder.has_table():
        recorder.migration_qs.filter(app="reversion").delete()


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
