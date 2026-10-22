"""The changeset attribution rename retains its released database column and data."""

from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.compose.migrations import RuntimeMigrations
from angee.provenance.models import Changeset
from angee.provenance.runtime_migrations import changeset_actor_ref
from tests.tables import model_tables


def _released() -> ProjectState:
    state = ProjectState()
    state.add_model(
        ModelState(
            "provenance",
            "Changeset",
            [("id", models.AutoField(primary_key=True)), ("actor", models.TextField(blank=True, editable=False))],
            options={"db_table": "test_provenance_actor_cutover"},
        )
    )
    return state


def test_actor_reference_cutover_is_discovered_once_and_matches_current_field(tmp_path: Path) -> None:
    migrations = RuntimeMigrations(
        (apps.get_app_config("provenance"),),
        runtime_dir=tmp_path,
        labels=("provenance",),
    )
    origin = ("angee.provenance:changeset_actor_ref",)
    released = _released()
    planned = migrations.schedule(released, only=origin)
    assert len(planned) == 1
    assert planned[0].origin == origin[0]
    upgraded = changeset_actor_ref.Migration("changeset_actor_ref", "provenance").mutate_state(released)
    assert migrations.schedule(upgraded, only=origin) == ()
    assert migrations.schedule(ProjectState(), only=origin) == ()
    field = upgraded.models["provenance", "changeset"].fields["actor_ref"]
    assert field.deconstruct()[1:] == Changeset._meta.get_field("actor_ref").deconstruct()[1:]
    assert "actor" not in upgraded.models["provenance", "changeset"].fields


@pytest.mark.django_db(transaction=True)
def test_actor_reference_cutover_preserves_native_column_and_stored_subjects() -> None:
    state = _released()
    old_model = state.apps.get_model("provenance", "Changeset")
    with model_tables((old_model,)):
        values = ["auth/user:123", "provenance/rule:456", ""]
        old_model.objects.bulk_create([old_model(actor=value) for value in values])
        with connection.schema_editor() as editor:
            state = changeset_actor_ref.Migration("changeset_actor_ref", "provenance").apply(state, editor)
        current = state.apps.get_model("provenance", "Changeset")
        assert list(current.objects.order_by("pk").values_list("actor_ref", flat=True)) == values
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, current._meta.db_table)
        assert [column.name for column in columns] == ["id", "actor"]
        assert current._meta.get_field("actor_ref").column == "actor"
