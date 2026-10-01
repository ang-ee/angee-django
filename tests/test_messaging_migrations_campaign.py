"""Populated historical follower, acknowledgement, and ownership transitions."""

from datetime import datetime, timezone

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ModelState, ProjectState

from angee.messaging.runtime_migrations import follower_party, notification_read_at, owner_column
from tests.tables import model_tables
from tests.test_runtime_migrations import isolated_upgrade_database as isolated_upgrade_database


@pytest.fixture
def historical_messaging(transactional_db, isolated_upgrade_database):
    """Reuse the migration suite's database and Django's historical MTI shape."""
    state = ProjectState()
    label, name = settings.AUTH_USER_MODEL.split(".")
    state.add_model(ModelState(label, name, [
        ("id", models.AutoField(primary_key=True)),
        ("username", models.CharField(max_length=80, default="")),
        ("email", models.CharField(max_length=120, default="")),
        ("first_name", models.CharField(max_length=80, default="")),
        ("last_name", models.CharField(max_length=80, default="")),
    ]))
    state.add_model(ModelState("parties", "Party", [
        ("id", models.AutoField(primary_key=True)),
        ("display_name", models.CharField(max_length=200)),
        ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
    ]))
    state.add_model(ModelState("parties", "Person", [
        ("party_ptr", models.OneToOneField("parties.Party", parent_link=True, primary_key=True,
                                          on_delete=models.CASCADE)),
        ("user", models.OneToOneField(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
    ], bases=("parties.party",)))
    state.add_model(ModelState("messaging", "Thread", [
        ("id", models.AutoField(primary_key=True)),
        ("channel_id", models.IntegerField(null=True)),
        ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
    ], options={"ordering": ("id",)}))
    state.add_model(ModelState("messaging", "ThreadFollower", [
        ("id", models.AutoField(primary_key=True)),
        ("sqid", models.CharField(max_length=30, default="")),
        ("thread", models.ForeignKey("messaging.Thread", on_delete=models.CASCADE)),
        ("user", models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
        ("notification_policy", models.CharField(max_length=10, default="inbox")),
        ("subtype_keys", models.JSONField(default=list)),
        ("last_read_message_id", models.IntegerField(null=True)),
        ("metadata", models.JSONField(default=dict)),
    ], options={
        "ordering": ("user_id", "sqid"),
        "constraints": [models.UniqueConstraint(fields=("thread", "user"), name="uq_thread_follower_thread_user")],
        "indexes": [models.Index(fields=("user", "thread"), name="messaging_t_user_id_88fea0_idx")],
    }))
    state.add_model(ModelState("messaging", "ThreadNotification", [
        ("id", models.AutoField(primary_key=True)),
        ("follower", models.ForeignKey("messaging.ThreadFollower", null=True, on_delete=models.SET_NULL)),
    ]))
    historical = [state.apps.get_model(app, model) for app, model in (
        (label, name), ("parties", "Party"), ("parties", "Person"), ("messaging", "Thread"),
        ("messaging", "ThreadFollower"), ("messaging", "ThreadNotification"),
    )]
    with model_tables(tuple(historical)):
        yield state


def test_follower_transition_preserves_identity_receipts_preferences_and_delivery_links(historical_messaging):
    before = historical_messaging
    user = before.apps.get_model(settings.AUTH_USER_MODEL)
    person = before.apps.get_model("parties", "Person")
    thread = before.apps.get_model("messaging", "Thread")
    follower = before.apps.get_model("messaging", "ThreadFollower")
    notification = before.apps.get_model("messaging", "ThreadNotification")
    existing_user = user.objects.create(username="existing")
    missing_user = user.objects.create(username="missing", first_name="New", last_name="Person")
    existing_person = person.objects.create(user=existing_user, display_name="Retained name", created_by=existing_user)
    threads = [thread.objects.create() for _ in range(2)]
    for index, (account, target) in enumerate(((existing_user, threads[0]), (missing_user, threads[0]),
                                              (missing_user, threads[1]))):
        row = follower.objects.create(thread=target, user=account, sqid=f"follow-{index}",
                                      notification_policy="email", subtype_keys=["comment"],
                                      last_read_message_id=40 + index, metadata={"retained": index})
        notification.objects.create(follower=row)
    original = list(follower.objects.order_by("pk").values())
    notices = list(notification.objects.order_by("pk").values())
    assert follower_party.applies(before)
    migration = follower_party.Migration("follower_party", "messaging")
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    upgraded = after.apps.get_model("messaging", "ThreadFollower")
    people = after.apps.get_model("parties", "Person")
    assert people.objects.get(user_id=existing_user.pk).pk == existing_person.pk
    assert people.objects.get(user_id=existing_user.pk).display_name == "Retained name"
    created = people.objects.get(user_id=missing_user.pk)
    assert created.display_name == "New Person" and created.created_by_id == missing_user.pk
    assert people.objects.count() == 2
    mapping = dict(people.objects.values_list("user_id", "pk"))
    expected = [{**{key: value for key, value in row.items() if key != "user_id"},
                 "party_id": mapping[row["user_id"]]} for row in original]
    assert list(upgraded.objects.order_by("pk").values()) == expected
    assert list(notification.objects.order_by("pk").values()) == notices
    assert not follower_party.applies(after)
    with pytest.raises(IntegrityError), transaction.atomic():
        upgraded.objects.create(thread_id=threads[0].pk, party_id=existing_person.pk)
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, upgraded._meta.db_table)
    assert constraints["uq_thread_follower_thread_party"]["unique"]
    assert constraints["ix_follower_party_thread"]["columns"] == ["party_id", "thread_id"]
    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    assert list(follower.objects.order_by("pk").values()) == original
    assert list(notification.objects.order_by("pk").values()) == notices


@pytest.mark.parametrize("values,expected", [
    ({"first_name": "  Given ", "last_name": "Name  ", "username": "fallback"}, "Given  Name"),
    ({"username": " account "}, "account"),
    ({"email": " contact@example.com "}, "contact@example.com"),
    ({}, "1"),
])
def test_follower_migration_uses_historical_person_name_fallbacks(historical_messaging, values, expected):
    state = historical_messaging
    user = state.apps.get_model(settings.AUTH_USER_MODEL).objects.create(**values)
    thread = state.apps.get_model("messaging", "Thread").objects.create()
    state.apps.get_model("messaging", "ThreadFollower").objects.create(user=user, thread=thread)
    with connection.schema_editor() as editor:
        after = follower_party.Migration("follower_party", "messaging").apply(state.clone(), editor)
    person = after.apps.get_model("parties", "Person").objects.get(user_id=user.pk)
    assert person.display_name == expected
    assert person.created_by_id == user.pk


def test_follower_reverse_refuses_accountless_parties_without_partially_rewriting_rows(historical_messaging):
    before = historical_messaging
    with connection.schema_editor() as editor:
        after = follower_party.Migration("follower_party", "messaging").apply(before.clone(), editor)
    party = after.apps.get_model("parties", "Party").objects.create(display_name="External")
    thread = after.apps.get_model("messaging", "Thread").objects.create()
    followers = after.apps.get_model("messaging", "ThreadFollower")
    followers.objects.create(party=party, thread=thread)
    with pytest.raises(IrreversibleError, match="without accounts"):
        follower_party.backwards(after.apps, connection.schema_editor())
    assert followers.objects.order_by().get().party_id == party.pk


def test_follower_migration_rejects_partial_state_and_skips_absent_or_completed_state(historical_messaging):
    assert not follower_party.applies(ProjectState())
    partial = historical_messaging.clone()
    partial.add_field("messaging", "threadfollower", "party",
                      models.ForeignKey("parties.Party", null=True, on_delete=models.CASCADE), preserve_default=True)
    with pytest.raises(ValueError, match="partial follower-party transition"):
        follower_party.applies(partial)
    partial.remove_field("messaging", "threadfollower", "user")
    assert not follower_party.applies(partial)


def test_follower_data_phase_is_repeatable_before_the_user_column_is_removed(historical_messaging):
    before = historical_messaging
    account = before.apps.get_model(settings.AUTH_USER_MODEL).objects.create(username="replay")
    thread = before.apps.get_model("messaging", "Thread").objects.create()
    original = before.apps.get_model("messaging", "ThreadFollower").objects.create(user=account, thread=thread)
    state = before.clone()
    with connection.schema_editor() as editor:
        for operation in follower_party.Migration.operations[:2]:
            previous = state.clone()
            operation.state_forwards("messaging", state)
            operation.database_forwards("messaging", editor, previous, state)
        follower_party.forwards(state.apps, editor)
        follower_party.forwards(state.apps, editor)
    people = state.apps.get_model("parties", "Person").objects
    assert people.count() == 1
    row = state.apps.get_model("messaging", "ThreadFollower").objects.get(pk=original.pk)
    assert row.user_id == account.pk and row.party_id == people.get().pk


def test_thread_ownership_backfill_excludes_channels_replays_transition_and_retains_attribution(historical_messaging):
    before = historical_messaging
    user = before.apps.get_model(settings.AUTH_USER_MODEL).objects.create(username="author")
    threads = before.apps.get_model("messaging", "Thread")
    native = threads.objects.create(created_by=user)
    channel = threads.objects.create(created_by=user, channel_id=42)
    unattended = threads.objects.create()
    assert owner_column.applies(before)
    migration = owner_column.Migration("owner_column", "messaging")
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    rows = after.apps.get_model("messaging", "Thread").objects
    expected = [(native.pk, user.pk, user.pk), (channel.pk, user.pk, None), (unattended.pk, None, None)]
    assert list(rows.order_by("pk").values_list("pk", "created_by_id", "owner_id")) == expected
    owner_column.forwards(after.apps, connection.schema_editor())
    assert list(rows.order_by("pk").values_list("pk", "created_by_id", "owner_id")) == expected
    assert not owner_column.applies(after)
    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    assert list(threads.objects.order_by("pk").values_list("pk", "created_by_id")) == [row[:2] for row in expected]


def test_notification_transition_leaves_existing_deliveries_unacknowledged(historical_messaging):
    before = historical_messaging
    original = before.apps.get_model("messaging", "ThreadNotification").objects.create()
    assert notification_read_at.applies(before)
    migration = notification_read_at.Migration("notification_read_at", "messaging")
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    notice = after.apps.get_model("messaging", "ThreadNotification").objects.get(pk=original.pk)
    assert notice.read_at is None
    notice.read_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    notice.save()
    assert not notification_read_at.applies(after)
    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    assert before.apps.get_model("messaging", "ThreadNotification").objects.filter(pk=original.pk).exists()
