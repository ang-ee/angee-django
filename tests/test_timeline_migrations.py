"""The declared cutover retires only the old derived ledger and its M2M table."""

from __future__ import annotations

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.timeline.runtime_migrations import rebuild_ledger
from tests.tables import model_tables


@pytest.mark.django_db(transaction=True)
def test_old_ledger_cutover_preserves_sources_and_retires_parent_and_needs() -> None:
    state = ProjectState()
    state.add_model(ModelState("timeline", "Source", [
        ("id", models.AutoField(primary_key=True)),
        ("name", models.CharField(max_length=50)),
    ], options={"db_table": "timeline_cutover_source"}))
    state.add_model(ModelState("timeline", "Event", [
        ("id", models.AutoField(primary_key=True)),
        ("key", models.CharField(max_length=500, unique=True)),
        ("target_id", models.CharField(max_length=100)),
        ("parent", models.ForeignKey("timeline.Event", on_delete=models.CASCADE, null=True)),
        ("needs", models.ManyToManyField("timeline.Source")),
    ], options={"db_table": "timeline_cutover_event"}))
    source = state.apps.get_model("timeline", "Source")
    event = state.apps.get_model("timeline", "Event")
    assert not rebuild_ledger.applies(ProjectState())
    assert rebuild_ledger.applies(state)
    with model_tables((source,)):
        with connection.schema_editor() as editor:
            editor.create_model(event)
        try:
            owner = source._base_manager.create(name="Owner data")
            parent = event._base_manager.create(key="run", target_id="old_public_id")
            child = event._base_manager.create(key="decision", parent=parent, target_id="another_public_id")
            child.needs.add(owner)
            with connection.schema_editor() as editor:
                retired = rebuild_ledger.Migration("rebuild_ledger", "timeline").apply(state, editor)
            assert not rebuild_ledger.applies(retired)
            assert ("timeline", "event") not in retired.models
            assert source._base_manager.get(pk=owner.pk).name == "Owner data"
            tables = connection.introspection.table_names()
            assert event._meta.db_table not in tables
            assert event.needs.through._meta.db_table not in tables
        finally:
            if event._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(event)
