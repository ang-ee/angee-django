"""The declared cutover retires only the old derived ledger and its M2M table."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.compose.runtime import Runtime
from angee.timeline.runtime_migrations import rebuild_ledger, retire_ledger
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("identity_field", ["key", "kind"])
def test_union_cutover_retires_either_ledger_shape_and_keeps_source_rows(identity_field):
    state = ProjectState()
    state.add_model(ModelState("timeline", "Source", [
        ("id", models.AutoField(primary_key=True)), ("name", models.CharField(max_length=50)),
    ], options={"db_table": "timeline_union_source"}))
    state.add_model(ModelState("timeline", "Event", [
        ("id", models.AutoField(primary_key=True)), (identity_field, models.CharField(max_length=100)),
    ], options={"db_table": "timeline_union_event"}))
    source, event = state.apps.get_model("timeline", "Source"), state.apps.get_model("timeline", "Event")
    assert not retire_ledger.applies(ProjectState())
    assert retire_ledger.applies(state)
    with model_tables((source,)):
        with connection.schema_editor() as editor:
            editor.create_model(event)
        try:
            owner = source.objects.create(name="Retained owner")
            event.objects.create(**{identity_field: "Derived occurrence"})
            with connection.schema_editor() as editor:
                retired = retire_ledger.Migration("retire_ledger", "timeline").apply(state, editor)
            assert not retire_ledger.applies(retired)
            assert source.objects.get(pk=owner.pk).name == "Retained owner"
            assert event._meta.db_table not in connection.introspection.table_names()
        finally:
            if event._meta.db_table in connection.introspection.table_names():
                with connection.schema_editor() as editor:
                    editor.delete_model(event)


def test_retiring_the_last_model_preserves_the_declared_migration_app(tmp_path):
    runtime = Runtime.discover((apps.get_app_config("timeline"),), runtime_dir=tmp_path / "runtime")
    assert runtime.labels == ("timeline",)
    sources = runtime.render_sources()
    assert Path("timeline/migrations/__init__.py") in sources
    assert "Event" not in sources[Path("timeline/models.py")]
    state = ProjectState()
    state.add_model(ModelState("timeline", "Event", [
        ("id", models.AutoField(primary_key=True)), ("kind", models.CharField(max_length=64)),
    ]))
    planned = runtime.runtime_migrations().schedule(state, only=("angee.timeline:retire_ledger",))
    assert len(planned) == 1
