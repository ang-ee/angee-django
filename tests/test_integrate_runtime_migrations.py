"""Rehearse integrate's populated, addon-owned runtime upgrades."""

from __future__ import annotations

import tomllib
from importlib import import_module
from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.base.fields import StateField
from angee.integrate.runtime_migrations import integration_concrete_type, record_link_target_names


def _state(*model_states: ModelState) -> ProjectState:
    state = ProjectState()
    for model_state in model_states:
        state.add_model(model_state)
    return state


def _content_type_state() -> ModelState:
    return ModelState.from_model(ContentType)


@pytest.mark.django_db(transaction=True)
def test_record_link_rename_preserves_both_target_columns() -> None:
    old = _state(
        _content_type_state(),
        ModelState(
            "integrate",
            "RecordLink",
            [
                ("id", models.AutoField(primary_key=True)),
                ("target_ct", models.ForeignKey("contenttypes.ContentType", null=True, on_delete=models.PROTECT)),
                ("target_id", models.CharField(max_length=255, null=True)),
            ],
            options={"db_table": "test_migration_record_link"},
        ),
    )
    assert record_link_target_names.applies(old)
    assert not record_link_target_names.applies(ProjectState.from_apps(apps))
    partial = old.clone()
    partial.models["integrate", "recordlink"].fields["target_content_type"] = partial.models[
        "integrate", "recordlink"
    ].fields.pop("target_ct")
    with pytest.raises(ValueError, match="partial generic target rename"):
        record_link_target_names.applies(partial)

    old_model = old.apps.get_model("integrate", "RecordLink")
    target_type = ContentType.objects.get_for_model(ContentType)
    migration = record_link_target_names.Migration("0002_record_link_target_names", "integrate")
    with connection.schema_editor() as editor:
        editor.create_model(old_model)
        old_model._base_manager.create(target_ct_id=target_type.pk, target_id=str(target_type.pk))
        upgraded = migration.apply(old, editor)

    new_model = upgraded.apps.get_model("integrate", "RecordLink")
    try:
        row = new_model._base_manager.get()
        assert row.target_content_type_id == target_type.pk
        assert row.target_object_id == str(target_type.pk)
        assert ContentType.objects.get(pk=row.target_object_id) == target_type
        assert set(upgraded.models["integrate", "recordlink"].fields) == {
            "id", "target_content_type", "target_object_id"
        }
        assert not record_link_target_names.applies(upgraded)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(new_model)


def _integration_state(*, feed_on_channel: bool) -> ProjectState:
    integration = ModelState(
        "integrate",
        "Integration",
        [
            ("id", models.AutoField(primary_key=True)),
            ("kind", models.CharField(max_length=80, default="Integration")),
        ],
        options={"db_table": "test_migration_integration"},
    )
    channel = ModelState(
        "messaging",
        "Channel",
        [
            (
                "integration_ptr",
                models.OneToOneField(
                    "integrate.Integration", on_delete=models.CASCADE,
                    parent_link=True, primary_key=True, auto_created=True,
                ),
            ),
        ],
        options={"db_table": "test_migration_channel"},
        bases=("integrate.integration",),
    )
    feed_parent = "messaging.Channel" if feed_on_channel else "integrate.Integration"
    feed = ModelState(
        "posts",
        "Feed",
        [
            (
                "channel_ptr" if feed_on_channel else "integration_ptr",
                models.OneToOneField(
                    feed_parent, on_delete=models.CASCADE,
                    parent_link=True, primary_key=True, auto_created=True,
                ),
            ),
        ],
        options={"db_table": "test_migration_feed"},
        bases=(feed_parent.lower(),),
    )
    directory = ModelState(
        "parties",
        "Directory",
        [
            (
                "integration_ptr",
                models.OneToOneField(
                    "integrate.Integration", on_delete=models.CASCADE,
                    parent_link=True, primary_key=True, auto_created=True,
                ),
            ),
        ],
        options={"db_table": "test_migration_directory"},
        bases=("integrate.integration",),
    )
    return _state(_content_type_state(), integration, channel, feed, directory)


def _exercise_concrete_type_backfill(feed_on_channel: bool) -> None:
    old = _integration_state(feed_on_channel=feed_on_channel)
    assert integration_concrete_type.applies(old)
    assert not integration_concrete_type.applies(ProjectState.from_apps(apps))
    partial = old.clone()
    partial.models["integrate", "integration"].fields["concrete_type"] = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.PROTECT, null=True
    )
    with pytest.raises(ValueError, match="partial concrete type transition"):
        integration_concrete_type.applies(partial)

    integration = old.apps.get_model("integrate", "Integration")
    channel = old.apps.get_model("messaging", "Channel")
    feed = old.apps.get_model("posts", "Feed")
    directory = old.apps.get_model("parties", "Directory")
    with connection.schema_editor() as editor:
        for model in (integration, channel, feed, directory):
            editor.create_model(model)
        base_id = integration._base_manager.create(kind="base").pk
        channel_id = integration._base_manager.create(kind="channel").pk
        feed_id = integration._base_manager.create(kind="feed").pk
        directory_id = integration._base_manager.create(kind="directory").pk
        quote = editor.quote_name
        for pk in (channel_id, feed_id) if feed_on_channel else (channel_id,):
            editor.execute(
                f"INSERT INTO {quote(channel._meta.db_table)} ({quote('integration_ptr_id')}) VALUES (%s)",
                [pk],
            )
        feed_column = "channel_ptr_id" if feed_on_channel else "integration_ptr_id"
        editor.execute(
            f"INSERT INTO {quote(feed._meta.db_table)} ({quote(feed_column)}) VALUES (%s)",
            [feed_id],
        )
        editor.execute(
            f"INSERT INTO {quote(directory._meta.db_table)} ({quote('integration_ptr_id')}) VALUES (%s)",
            [directory_id],
        )
        upgraded = integration_concrete_type.Migration("0003_integration_concrete_type", "integrate").apply(
            old, editor
        )

    new_integration = upgraded.apps.get_model("integrate", "Integration")
    try:
        assert {
            row.pk: (row.concrete_type.app_label, row.concrete_type.model)
            for row in new_integration._base_manager.order_by("pk")
        } == {
            base_id: ("integrate", "integration"),
            channel_id: ("messaging", "channel"),
            feed_id: ("posts", "feed"),
            directory_id: ("parties", "directory"),
        }
        assert not integration_concrete_type.applies(upgraded)
    finally:
        with connection.schema_editor() as editor:
            for model in (feed, directory, channel, new_integration):
                editor.delete_model(model)


@pytest.mark.parametrize("feed_on_channel", [False, True], ids=["before-feed-reparent", "after-feed-reparent"])
@pytest.mark.django_db(transaction=True)
def test_concrete_type_backfill_selects_deepest_existing_child(feed_on_channel: bool) -> None:
    _exercise_concrete_type_backfill(feed_on_channel)


@pytest.mark.parametrize("feed_on_channel", [False, True], ids=["before-feed-reparent", "after-feed-reparent"])
@pytest.mark.django_db(transaction=True)
def test_postgresql_concrete_type_backfill(feed_on_channel: bool) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("The populated PostgreSQL migration is rehearsed against PostgreSQL.")
    _exercise_concrete_type_backfill(feed_on_channel)


@pytest.mark.django_db(transaction=True)
def test_concrete_type_backfill_accepts_an_empty_database() -> None:
    old = _integration_state(feed_on_channel=True)
    old_model = old.apps.get_model("integrate", "Integration")
    with connection.schema_editor() as editor:
        editor.create_model(old_model)
        upgraded = integration_concrete_type.Migration("0003_empty_integration", "integrate").apply(old, editor)
    new_model = upgraded.apps.get_model("integrate", "Integration")
    try:
        assert not new_model._base_manager.exists()
        assert not integration_concrete_type.applies(upgraded)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(new_model)


@pytest.mark.django_db(transaction=True)
def test_concrete_type_backfill_rejects_sibling_children_for_one_row() -> None:
    old = _integration_state(feed_on_channel=True)
    integration = old.apps.get_model("integrate", "Integration")
    channel = old.apps.get_model("messaging", "Channel")
    feed = old.apps.get_model("posts", "Feed")
    directory = old.apps.get_model("parties", "Directory")
    with connection.schema_editor() as editor:
        for model in (integration, channel, feed, directory):
            editor.create_model(model)
        pk = integration._base_manager.create(kind="ambiguous").pk
        for child in (channel, directory):
            editor.execute(
                f"INSERT INTO {editor.quote_name(child._meta.db_table)} "
                f"({editor.quote_name('integration_ptr_id')}) VALUES (%s)",
                [pk],
            )
        with pytest.raises(ValueError, match="incompatible children"):
            integration_concrete_type.Migration("0003_ambiguous_integration", "integrate").apply(old, editor)
    with connection.schema_editor() as editor:
        for model in (feed, directory, channel, integration):
            editor.delete_model(model)


BRIDGE_CASES = (
    ("messaging", "channel_sync_status", "Channel"),
    ("parties", "directory_sync_status", "Directory"),
    ("integrate_vcs", "vcs_bridge_sync_status", "VcsBridge"),
    ("storage_integrate", "mount_sync_status", "Mount"),
    ("posts", "feed_sync_status", "Feed"),
)


@pytest.mark.parametrize("addon,source,model_name", BRIDGE_CASES)
@pytest.mark.django_db(transaction=True)
def test_bridge_status_migration_preserves_failure_and_other_stages(
    addon: str, source: str, model_name: str
) -> None:
    migration_module = import_module(f"angee.{addon}.runtime_migrations.{source}")
    old = _state(
        ModelState(
            addon,
            model_name,
            [
                ("id", models.AutoField(primary_key=True)),
                ("sync_stage", models.CharField(max_length=32, default="idle")),
                ("last_sync_status", models.CharField(max_length=64, blank=True)),
                ("last_sync_summary", models.JSONField(default=dict)),
            ],
            options={"db_table": f"test_migration_{addon}_sync_status"},
        ),
    )
    assert migration_module.applies(old)
    assert not migration_module.applies(ProjectState.from_apps(apps))
    partial = old.clone()
    partial.models[addon, model_name.lower()].fields["sync_stage"] = StateField(
        choices=[("idle", "Idle")], default="idle"
    )
    with pytest.raises(ValueError, match="partial sync status transition"):
        migration_module.applies(partial)

    old_model = old.apps.get_model(addon, model_name)
    with connection.schema_editor() as editor:
        editor.create_model(old_model)
        failed_id = old_model._base_manager.create(sync_stage="completed", last_sync_status="error").pk
        queued_id = old_model._base_manager.create(sync_stage="queued", last_sync_status="ok").pk
        idle_id = old_model._base_manager.create(sync_stage="idle", last_sync_status="").pk
        upgraded = migration_module.Migration(f"0002_{source}", addon).apply(old, editor)

    new_model = upgraded.apps.get_model(addon, model_name)
    try:
        assert dict(new_model._base_manager.values_list("pk", "sync_stage")) == {
            failed_id: "failed", queued_id: "queued", idle_id: "idle"
        }
        stage_field = upgraded.models[addon, model_name.lower()].fields["sync_stage"]
        assert isinstance(stage_field, StateField)
        current = ProjectState.from_apps(apps)
        key = ("messaging", "channel") if addon == "posts" else (addon, model_name.lower())
        expected = current.models[key]
        assert stage_field.deconstruct()[1:] == expected.fields["sync_stage"].deconstruct()[1:]
        assert not migration_module.applies(upgraded)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(new_model)


@pytest.mark.django_db(transaction=True)
def test_empty_bridge_status_migration_and_unrecognized_value_rejection() -> None:
    module = import_module("angee.messaging.runtime_migrations.channel_sync_status")
    old = _state(
        ModelState(
            "messaging", "Channel",
            [
                ("id", models.AutoField(primary_key=True)),
                ("sync_stage", models.CharField(max_length=32, default="idle")),
                ("last_sync_status", models.CharField(max_length=64, blank=True)),
                ("last_sync_summary", models.JSONField(default=dict)),
            ],
            options={"db_table": "test_migration_empty_channel"},
        )
    )
    model = old.apps.get_model("messaging", "Channel")
    with connection.schema_editor() as editor:
        editor.create_model(model)
        model._base_manager.create(sync_stage="unknown", last_sync_status="error")
        with pytest.raises(ValueError, match="outside the SyncStage enum"):
            module.carry_sync_failure(old.apps, editor)
        model._base_manager.all().delete()
        model._base_manager.create(sync_stage="idle", last_sync_status="unexpected")
        with pytest.raises(ValueError, match="unknown last sync status"):
            module.carry_sync_failure(old.apps, editor)
        model._base_manager.all().delete()
        upgraded = module.Migration("0002_empty_channel", "messaging").apply(old, editor)
    try:
        assert not upgraded.apps.get_model("messaging", "Channel")._base_manager.exists()
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(upgraded.apps.get_model("messaging", "Channel"))


def test_posts_declares_the_sync_status_migration_before_the_channel_parent_move() -> None:
    """Feed's status column must be carried and dropped before Feed moves under Channel."""

    manifest = tomllib.loads((Path(import_module("angee.posts").__file__).parent / "addon.toml").read_text())
    names = [entry["name"] for entry in manifest["migrations"]]
    assert names.index("feed_sync_status") < names.index("feed_channel_parent")
