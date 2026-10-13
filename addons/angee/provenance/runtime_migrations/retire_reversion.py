"""Retire django-reversion after it leaves the composed app set.

All Version and Revision data is gone by ruling (MarkdownPage body history,
Project title/body history included); it is not converted to native history.
Back the database up before upgrading if that history matters. Reverse is a no-op
and cannot restore that data or migration records. Run
``manage.py remove_stale_contenttypes`` after the upgrade to remove stale
``reversion.*`` content types and their permissions through Django's cleanup owner.

The guard waits for the statement principals (``Rule``/``Seed``): the drop is the
first destructive declaration of the provenance chain, and attaching it one round
after the chain's schema declarations lets the composer order it after every
round-zero declaration, the parties fold preflight among them, so no data is
dropped before that preflight has named every blocking row.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    if ("provenance", "changeset") not in state.models or any(label == "reversion" for label, _ in state.models):
        return False
    # statement_principal_schema is pending in this build; wait for its round.
    return all(("provenance", name) in state.models for name in ("rule", "seed"))


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
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
