"""New native history tables checkpoint existing rows through frozen migrations."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connections, migrations, models, router, transaction
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ModelState, ProjectState
from django.db.migrations.writer import MigrationWriter
from django.test.utils import isolate_apps
from django.utils import timezone
from simple_history.models import HistoricalRecords

from angee.base.history_migrations import HistoryBaseline
from angee.compose import migrations as migration_owner
from angee.compose.migrations import DropGuardAutodetector


@pytest.fixture
def native_history_states(monkeypatch):
    with isolate_apps("tests", "django.contrib.contenttypes") as registry:

        class Tracked(models.Model):
            audit = HistoricalRecords(
                inherit=True,
                app="contenttypes",
                custom_model_name=lambda name: f"Audit{name}",
                excluded_fields=("secret",),
                history_user_id_field=models.IntegerField(null=True),
            )

            class Meta:
                abstract = True
                app_label = "tests"

        class CheckpointSource(Tracked):
            title = models.CharField(max_length=80)
            secret = models.CharField(max_length=80)
            anchor = models.ForeignKey("tests.CheckpointAnchor", models.SET_NULL, null=True)

            class Meta:
                app_label = "tests"

        class CheckpointAnchor(models.Model):
            # A real schema cycle forces Django to defer a foreign key.
            snapshot = models.ForeignKey("contenttypes.AuditCheckpointSource", models.SET_NULL, null=True)

            class Meta:
                app_label = "tests"

        class HistoricalDecoy(models.Model):
            title = models.CharField(max_length=80)

            class Meta:
                app_label = "tests"

        current = ProjectState.from_apps(registry)
        previous_source = ModelState.from_model(CheckpointSource)
        previous_source.fields.pop("anchor")
        previous = ProjectState({("tests", "checkpointsource"): previous_source})
        initial = migrations.Migration("0001_source", "tests")
        initial.operations = [migrations.CreateModel(
            "CheckpointSource", list(previous_source.fields.items()), options=previous_source.options,
        )]
        graph = MigrationGraph()
        graph.add_node(("tests", initial.name), initial)
        monkeypatch.setattr(migration_owner, "apps", registry)
        yield previous, current, graph


@pytest.mark.parametrize("trim", (None, {"contenttypes"}))
def test_descriptor_mapping_waits_for_complete_cross_app_schema(native_history_states, trim):
    previous, current, graph = native_history_states
    changes = DropGuardAutodetector(
        previous, current, MigrationQuestioner({"ask_initial": True}),
    ).changes(graph, trim_to_apps=trim)
    baseline = changes["contenttypes"][-1]
    assert len(baseline.operations) == 1
    operation = baseline.operations[0]
    assert isinstance(operation, HistoryBaseline)
    assert operation.source_model == "tests.checkpointsource"
    assert operation.history_model == "contenttypes.auditcheckpointsource"
    assert ("anchor_id", "anchor_id") in operation.fields
    assert not any(origin == "secret" for origin, _target in operation.fields)
    assert operation.null_fields == ("history_user_id",)
    assert ("contenttypes", changes["contenttypes"][-2].name) in baseline.dependencies
    assert ("tests", changes["tests"][-1].name) in baseline.dependencies
    for app_label, app_migrations in changes.items():
        for migration in app_migrations:
            graph.add_node((app_label, migration.name), migration)
    for app_label, app_migrations in changes.items():
        for migration in app_migrations:
            for dependency in migration.dependencies:
                graph.add_dependency(migration, (app_label, migration.name), dependency)
    graph.ensure_not_cyclic()
    plan = graph.forwards_plan(("contenttypes", baseline.name))
    state = ProjectState()
    for node in plan[:-1]:
        state = graph.nodes[node].mutate_state(state)
    assert state.apps.get_model(operation.source_model)._meta.get_field("anchor").is_relation
    assert state.apps.get_model(operation.history_model)._meta.get_field("anchor").is_relation
    assert state.models.keys() == current.models.keys()

    # Emitted migrations can reconstruct the operation without the live models.
    emitted = MigrationWriter(baseline).as_string(include_header=False)
    namespace = {}
    exec(emitted, namespace)
    frozen = namespace["Migration"].operations[0]
    assert frozen.deconstruct() == operation.deconstruct()
    assert "angee.compose" not in emitted


def test_new_source_models_do_not_need_baselines(native_history_states):
    _previous, current, _graph = native_history_states
    changes = DropGuardAutodetector(
        ProjectState(), current, MigrationQuestioner({"ask_initial": True}),
    ).changes(MigrationGraph())
    assert changes
    assert not any(
        isinstance(operation, HistoryBaseline)
        for app_migrations in changes.values()
        for migration in app_migrations
        for operation in migration.operations
    )


def test_existing_native_history_is_not_checkpointed_again(native_history_states):
    _previous, current, graph = native_history_states
    assert DropGuardAutodetector(current, current).changes(graph) == {}


def test_trim_retains_pending_parent_columns_copied_into_child_history(monkeypatch):
    with isolate_apps("tests", "django.contrib.contenttypes", "django.contrib.auth") as registry:

        class CheckpointParent(models.Model):
            title = models.CharField(max_length=80)
            new_value = models.IntegerField(null=True)

            class Meta:
                app_label = "contenttypes"

        class CheckpointChild(CheckpointParent):
            detail = models.CharField(max_length=80)
            audit = HistoricalRecords(app="auth", history_user_id_field=models.IntegerField(null=True))

            class Meta:
                app_label = "tests"

        current = ProjectState.from_apps(registry)
        parent = ModelState.from_model(CheckpointParent)
        parent.fields.pop("new_value")
        child = ModelState.from_model(CheckpointChild)
        previous = ProjectState({
            ("contenttypes", "checkpointparent"): parent,
            ("tests", "checkpointchild"): child,
        })
        graph = MigrationGraph()
        for label in ("contenttypes", "tests"):
            initial = migrations.Migration("0001_initial", label)
            graph.add_node((label, initial.name), initial)
        graph.add_dependency(None, ("tests", "0001_initial"), ("contenttypes", "0001_initial"))
        monkeypatch.setattr(migration_owner, "apps", registry)
        changes = DropGuardAutodetector(
            previous, current, MigrationQuestioner({"ask_initial": True}),
        ).changes(graph, trim_to_apps={"auth"})
        baseline = changes["auth"][-1]
        assert isinstance(baseline.operations[0], HistoryBaseline)
        assert ("new_value", "new_value") in baseline.operations[0].fields
        assert ("checkpointparent_ptr_id", "checkpointparent_ptr_id") in baseline.operations[0].fields
        assert ("contenttypes", changes["contenttypes"][-1].name) in baseline.dependencies
        assert any(
            isinstance(operation, migrations.AddField) and operation.name == "new_value"
            for migration in changes["contenttypes"] for operation in migration.operations
        )


@pytest.fixture
def checkpoint_store(django_db_blocker):
    """Only historical models, on a separate alias: no live history signals."""

    state = ProjectState({
        ("checkpoint", "record"): ModelState("checkpoint", "Record", [
            ("token", models.CharField(primary_key=True, max_length=30)),
            ("title", models.CharField(max_length=80)),
            ("created_at", models.DateTimeField()),
            ("changeset_id", models.IntegerField(null=True)),
        ]),
        ("checkpoint", "snapshot"): ModelState("checkpoint", "Snapshot", [
            ("history_id", models.AutoField(primary_key=True)),
            ("record_key", models.CharField(max_length=30)),
            ("title", models.CharField(max_length=80)),
            ("created_at", models.DateTimeField()),
            ("history_date", models.DateTimeField()),
            ("history_type", models.CharField(max_length=1)),
            ("history_change_reason", models.CharField(max_length=100, null=True)),
            ("history_user_id", models.IntegerField(null=True)),
            ("changeset_id", models.IntegerField(null=True)),
        ]),
    })
    alias = "history_checkpoint"
    connections.databases[alias] = {
        **connections["default"].settings_dict,
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "OPTIONS": {},
        "TIME_ZONE": None,
    }
    database = connections[alias]
    source = state.apps.get_model("checkpoint.Record")
    history = state.apps.get_model("checkpoint.Snapshot")
    operation = HistoryBaseline(
        "checkpoint.record", "checkpoint.snapshot",
        (("token", "record_key"), ("title", "title"), ("created_at", "created_at"), ("changeset_id", "changeset_id")),
        ("history_user_id", "changeset_id"),
    )
    try:
        with django_db_blocker.unblock():
            with database.schema_editor() as editor:
                editor.create_model(source)
                editor.create_model(history)
            yield state, database, source, history, operation
    finally:
        database.close()
        del connections[alias]
        del connections.databases[alias]


def test_baseline_is_an_upgrade_observation_and_preserves_prior_captures(checkpoint_store):
    state, database, source, history, operation = checkpoint_store
    alias = database.alias
    old_date = timezone.now() - timedelta(days=90)
    sources = source._base_manager.using(alias)
    snapshots = history._base_manager.using(alias)
    sources.bulk_create([
        source(token="untracked", title="Stored old value", created_at=old_date, changeset_id=12),
        source(token="captured", title="Current value", created_at=old_date, changeset_id=13),
    ])
    captured = snapshots.create(
        record_key="captured", title="Prior value", created_at=old_date,
        history_date=old_date, history_type="~", history_user_id=4, changeset_id=7,
    )
    before = timezone.now()
    with database.schema_editor() as editor, transaction.atomic(using=alias):
        operation.database_forwards("checkpoint", editor, state, state)
    baseline = snapshots.get(record_key="untracked")
    assert baseline.title == "Stored old value"
    assert baseline.created_at == old_date
    assert before <= baseline.history_date <= timezone.now()
    assert baseline.history_type == "~"
    assert baseline.history_user_id is None
    assert baseline.changeset_id is None
    assert snapshots.filter(record_key="captured").count() == 1
    captured.refresh_from_db(using=alias)
    assert (captured.title, captured.history_user_id, captured.changeset_id) == ("Prior value", 4, 7)

    sources.filter(token="untracked").update(title="Later edit")
    real = snapshots.create(
        record_key="untracked", title="Later edit", created_at=old_date,
        history_date=timezone.now(), history_type="~", history_user_id=9, changeset_id=10,
    )
    with database.schema_editor() as editor, transaction.atomic(using=alias):
        operation.database_backwards("checkpoint", editor, state, state)
        operation.database_forwards("checkpoint", editor, state, state)
    assert snapshots.count() == 3
    assert snapshots.get(pk=baseline.pk).title == "Stored old value"
    assert snapshots.get(pk=real.pk).changeset_id == 10
    assert sources.get(token="untracked").title == "Later edit"


@pytest.mark.parametrize("denied", ("record", "snapshot"))
def test_router_can_refuse_either_side_without_querying(checkpoint_store, monkeypatch, denied):
    state, database, _source, _history, operation = checkpoint_store
    calls = []

    def allow_migrate(alias, model):
        calls.append((alias, model._meta.label_lower))
        return model._meta.model_name != denied

    monkeypatch.setattr(router, "allow_migrate_model", allow_migrate)
    with database.execute_wrapper(lambda *args: pytest.fail("A refused checkpoint queried the database")):
        operation.database_forwards("checkpoint", SimpleNamespace(connection=database), state, state)
    assert calls[0] == (database.alias, "checkpoint.record")
    if denied == "snapshot":
        assert calls[1] == (database.alias, "checkpoint.snapshot")
