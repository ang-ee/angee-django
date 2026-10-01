"""Rehearse the populated Feed → Channel MTI upgrade on PostgreSQL."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection, models
from django.db.migrations.state import ProjectState
from rebac import actor_context, system_context

from angee.posts.runtime_migrations import feed_channel_parent
from tests.conftest import Feed, make_integration
from tests.integrate_models import Integration
from tests.messaging_models import Channel, Message


def _legacy_state() -> ProjectState:
    state = ProjectState.from_apps(apps)
    feed = state.models[("posts", "feed")]
    channel = state.models[("messaging", "channel")]
    feed.options["db_table"] = "test_posts_legacy_feed"
    feed.bases = ("integrate.integration", models.Model)
    feed.fields["integration_ptr"] = channel.fields["integration_ptr"].clone()
    del feed.fields["channel_ptr"]
    feed.fields["backend_class"] = feed.fields.pop("feed_backend_class")
    for name in feed_channel_parent.BRIDGE_FIELDS:
        feed.fields[name] = channel.fields[name].clone()
    return state


def _insert_legacy_feed(editor: Any, model: type[models.Model], pk: Any) -> None:
    values = {
        "integration_ptr": pk,
        "backend_class": "stub",
        "config": {"legacy": "retained"},
        "cursor": {"last": "post-12"},
        "sync_stage": "completed",
        "last_sync_items": 12,
    }
    columns = []
    params = []
    for field in model._meta.local_concrete_fields:
        columns.append(editor.quote_name(field.column))
        value = values.get(field.name, field.get_default() if field.has_default() else None)
        params.append(field.get_db_prep_save(value, editor.connection))
    placeholders = ", ".join("%s" for _ in params)
    sql = f"INSERT INTO {editor.quote_name(model._meta.db_table)} ({', '.join(columns)}) VALUES ({placeholders})"
    editor.execute(sql, params)


@pytest.mark.django_db(transaction=True)
def test_populated_feed_upgrade_preserves_rows_messages_and_channel_reader() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("The live MTI upgrade is rehearsed against PostgreSQL.")

    call_command("rebac", "sync", verbosity=0)
    legacy = _legacy_state()
    assert feed_channel_parent.applies(legacy)
    assert not feed_channel_parent.applies(ProjectState.from_apps(apps))
    partial = legacy.clone()
    partial.models[("posts", "feed")].bases = ("messaging.channel", models.Model)
    with pytest.raises(ValueError, match="partial Channel parent transition"):
        feed_channel_parent.applies(partial)
    old_feed = legacy.apps.get_model("posts", "Feed")
    reader = get_user_model().objects.create_user(username="feed-migration-reader")
    with system_context(reason="test.posts.feed_migration.setup"):
        integration = make_integration("feed-migration", model=Integration)
        Integration._base_manager.filter(pk=integration.pk).update(
            concrete_type=ContentType.objects.get_for_model(Feed)
        )

    migration = feed_channel_parent.Migration("0002_feed_channel_parent", "posts")
    with connection.schema_editor() as editor:
        editor.create_model(old_feed)
        _insert_legacy_feed(editor, old_feed, integration.pk)
        with system_context(reason="test.posts.feed_migration.message"):
            message = Message._base_manager.create(channel_id=integration.pk, external_id="legacy-post")
        upgraded = migration.apply(legacy, editor)

    try:
        expected = ProjectState.from_apps(apps).models[("posts", "feed")]
        actual = upgraded.models[("posts", "feed")]
        assert actual.bases == expected.bases
        assert set(actual.fields) == set(expected.fields)
        for name in expected.fields:
            assert actual.fields[name].deconstruct()[1:] == expected.fields[name].deconstruct()[1:]
        new_feed = upgraded.apps.get_model("posts", "Feed")
        feed = new_feed._base_manager.get(pk=integration.pk)
        channel = Channel._base_manager.get(pk=integration.pk)
        message.refresh_from_db()
        assert feed.pk == integration.pk == channel.pk == message.channel_id
        assert feed.feed_backend_class == "stub"
        assert channel.backend_class == "feed"
        assert channel.config == {"legacy": "retained"}
        assert channel.cursor == {"last": "post-12"}
        assert channel.sync_stage == "completed"
        assert channel.last_sync_items == 12
        with system_context(reason="test.posts.feed_migration.kind"):
            assert not Channel.objects.of_concrete_type().filter(pk=integration.pk).exists()
        assert not feed_channel_parent.applies(upgraded)

        with actor_context(integration.owner):
            channel.grant_record_access("reader", reader)
        assert Message.objects.with_actor(reader).filter(pk=message.pk).exists()
        assert channel.with_actor(reader).has_access("read")
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(new_feed)
