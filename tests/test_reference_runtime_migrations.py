"""Replay frozen cleanup and ownership migrations against historical models."""

import importlib

import pytest
from django.conf import settings
from django.db import connection, migrations, models
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState, ProjectState

from tests.tables import model_tables
from tests.test_runtime_migrations import isolated_upgrade_database  # noqa: F401


@pytest.fixture
def historical_rebac(db):
    loader = MigrationLoader(None)
    return loader.project_state(loader.graph.leaf_nodes("rebac"))


@pytest.mark.parametrize("addon", ["tags", "uom", "portfolio"])
def test_tuple_cleanup_removes_only_retired_user_wildcards_in_both_stores(historical_rebac, addon):
    module = importlib.import_module(f"angee.{addon}.runtime_migrations.shared_reader_cleanup")
    assert module.applies(historical_rebac)
    registry = historical_rebac.apps
    relationship = registry.get_model("rebac", "Relationship")
    normalized = registry.get_model("rebac", "RelationshipRegistry")
    resource = registry.get_model("rebac", "RebacResource")
    retired = module.RETIRED_RELATION
    targets = [(kind, retired, "auth/user", "*", "") for kind in module.RESOURCE_TYPES]
    kind = module.RESOURCE_TYPES[0]
    retained = [
        (kind, retired, "auth/user", "42", ""),
        (kind, retired, "auth/group", "*", ""),
        (kind, retired, "auth/user", "*", "member"),
        (kind, "editor", "auth/user", "*", ""),
        ("other/row", retired, "auth/user", "*", ""),
    ]
    keep_ids = {relationship: set(), normalized: set()}
    for index, (target, relation, subject_type, subject_id, subject_relation) in enumerate(targets + retained):
        values = {"relation": relation, "optional_subject_relation": subject_relation}
        plain = relationship.objects.create(
            resource_type=target, resource_id=str(index), subject_type=subject_type, subject_id=subject_id, **values,
        )
        subject, _created = resource.objects.get_or_create(resource_type=subject_type, resource_id=subject_id)
        target_row = resource.objects.create(resource_type=target, resource_id=str(index))
        registered = normalized.objects.create(resource_fk=target_row, subject_fk=subject, **values)
        if index >= len(targets):
            keep_ids[relationship].add(plain.pk)
            keep_ids[normalized].add(registered.pk)
    resources_before = list(resource.objects.order_by("pk").values())
    operation = module.Migration.operations[0]
    editor = connection.schema_editor()
    for _ in range(2):
        operation.database_forwards(addon, editor, historical_rebac, historical_rebac)
        for store, ids in keep_ids.items():
            assert set(store.objects.values_list("pk", flat=True)) == ids
        assert list(resource.objects.order_by("pk").values()) == resources_before
    operation.database_backwards(addon, editor, historical_rebac, historical_rebac)
    for store, ids in keep_ids.items():
        assert set(store.objects.values_list("pk", flat=True)) == ids
    assert list(resource.objects.order_by("pk").values()) == resources_before


@pytest.mark.parametrize("addon", ["tags", "uom", "portfolio"])
def test_tuple_cleanup_honors_router_denial_without_queries(
    historical_rebac, addon, settings, django_assert_num_queries,
):
    class DenyStores:
        def allow_migrate(self, db, app_label, model_name=None, **_hints):
            return False if model_name in {"relationship", "relationshipregistry"} else None

    settings.DATABASE_ROUTERS = [DenyStores()]
    module = importlib.import_module(f"angee.{addon}.runtime_migrations.shared_reader_cleanup")
    with django_assert_num_queries(0):
        module.forwards(historical_rebac.apps, connection.schema_editor())


@pytest.mark.parametrize("addon", ["tags", "uom", "portfolio"])
def test_tuple_cleanup_waits_for_both_historical_stores(addon):
    module = importlib.import_module(f"angee.{addon}.runtime_migrations.shared_reader_cleanup")
    state = ProjectState()
    assert not module.applies(state)
    state.add_model(ModelState("rebac", "Relationship", []))
    assert not module.applies(state)
    state.add_model(ModelState("rebac", "RelationshipRegistry", []))
    assert module.applies(state)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_upgrade_database")
def test_notes_owner_migration_backfills_reruns_and_reverses_without_losing_attribution():
    module = importlib.import_module("example.notes.runtime_migrations.owner_column")
    user_label, user_name = settings.AUTH_USER_MODEL.split(".")
    state = ProjectState()
    state.add_model(ModelState(user_label, user_name, [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("notes", "Note", [
        ("id", models.AutoField(primary_key=True)),
        ("title", models.CharField(max_length=40)),
        ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
    ]))
    author_model = state.apps.get_model(user_label, user_name)
    old_note = state.apps.get_model("notes", "Note")
    with model_tables((author_model, old_note)):
        author = author_model.objects.create()
        authored = old_note.objects.create(title="Authored", created_by_id=author.pk)
        unattended = old_note.objects.create(title="Unattended")
        assert module.applies(state)
        migration = module.Migration("owner_column", "notes")
        with connection.schema_editor() as editor:
            upgraded = migration.apply(state.clone(), editor)
        note = upgraded.apps.get_model("notes", "Note")
        expected = [(authored.pk, author.pk, author.pk), (unattended.pk, None, None)]
        assert list(note.objects.order_by("pk").values_list("pk", "created_by_id", "owner_id")) == expected
        assert not module.applies(upgraded)
        module.forwards(upgraded.apps, connection.schema_editor())
        assert list(note.objects.order_by("pk").values_list("pk", "created_by_id", "owner_id")) == expected
        data_operation = migration.operations[1]
        assert isinstance(data_operation, migrations.RunPython)
        data_operation.database_backwards("notes", connection.schema_editor(), upgraded, upgraded)
        assert list(note.objects.order_by("pk").values_list("pk", "created_by_id", "owner_id")) == expected
        with connection.schema_editor() as editor:
            migration.unapply(state, editor)
        assert list(old_note.objects.order_by("pk").values_list("pk", "created_by_id", "title")) == [
            (authored.pk, author.pk, "Authored"), (unattended.pk, None, "Unattended"),
        ]


def test_notes_owner_migration_skips_absent_models_and_refuses_missing_attribution():
    module = importlib.import_module("example.notes.runtime_migrations.owner_column")
    state = ProjectState()
    assert not module.applies(state)
    state.add_model(ModelState("notes", "Note", [("id", models.AutoField(primary_key=True))]))
    with pytest.raises(ValueError, match="created_by"):
        module.applies(state)
