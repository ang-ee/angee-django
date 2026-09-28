"""PostgreSQL serialization of roster review and holder preference writes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context

from angee.messaging.models import NotificationPolicy
from tests.spaces_campaign_helpers import roster as roster
from tests.spaces_campaign_helpers import spaces_storage as spaces_storage
from tests.spaces_models import Membership
from tests.test_spaces import spaces_tables as spaces_tables

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL roster row-lock and two-writer contract"),
]


def in_connection(call):
    """Follow the suite's independent-connection and bounded-lock-wait pattern."""
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def test_preference_write_locks_one_roster_row_before_authorizing(roster):
    actor = roster.actors["member"]
    row = roster.rows["member"].with_actor(actor)
    with actor_context(actor), CaptureQueriesContext(connection) as queries:
        row.set_notifications(NotificationPolicy.EMAIL, ["comment"])
    locks = [query["sql"] for query in queries
             if Membership._meta.db_table in query["sql"] and "FOR UPDATE" in query["sql"]]
    assert len(locks) == 1


def test_two_preference_writers_preserve_one_complete_policy_and_subtype_pair(roster):
    actor = roster.actors["member"]
    pk = roster.rows["member"].pk
    ready = Barrier(2)
    choices = ((NotificationPolicy.EMAIL, ["comment"]), (NotificationPolicy.MUTED, ["note"]))

    def write(policy, keys):
        row = Membership._base_manager.get(pk=pk).with_actor(actor)
        ready.wait(timeout=5)
        with actor_context(actor):
            return row.set_notifications(policy, keys).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(in_connection, lambda policy=policy, keys=keys: write(policy, keys))
                   for policy, keys in choices]
        assert [future.result(timeout=15) for future in futures] == [pk, pk]
    row = Membership._base_manager.get(pk=pk)
    assert (row.notification_policy, row.subtype_keys) in choices


def test_pending_preference_writer_observes_dismissal_after_waiting_for_row_lock(roster):
    actor = roster.actors["member"]
    pk = roster.rows["member"].pk
    attempted_lock = Event()

    def write():
        row = Membership._base_manager.get(pk=pk).with_actor(actor)

        def observe(execute, sql, params, many, context):
            if Membership._meta.db_table in sql and "FOR UPDATE" in sql:
                attempted_lock.set()
            return execute(sql, params, many, context)

        with actor_context(actor), connection.execute_wrapper(observe), pytest.raises(PermissionDenied):
            row.set_notifications(NotificationPolicy.EMAIL, ["comment"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Membership._base_manager.select_for_update().get(pk=pk)
            Membership._base_manager.filter(pk=pk).update(is_dismissed=True)
            future = pool.submit(in_connection, write)
            assert attempted_lock.wait(timeout=5)
        future.result(timeout=15)
    row = Membership._base_manager.get(pk=pk)
    assert row.is_dismissed
    assert row.notification_policy == "inbox" and row.subtype_keys == []


def test_two_moderators_reconfirm_the_same_existing_roster_pair(roster):
    row = roster.rows["member"]
    Membership._base_manager.filter(pk=row.pk).update(is_confirmed=False, is_dismissed=True)
    actor = roster.actors["moderator"]
    ready = Barrier(2)

    def reconfirm():
        ready.wait(timeout=5)
        with actor_context(actor):
            return Membership.objects.add_confirmed(
                group=roster.group, party=roster.people["member"], role="member",
            ).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(in_connection, reconfirm) for _ in range(2)]
        assert [future.result(timeout=15) for future in futures] == [row.pk, row.pk]
    row.refresh_from_db()
    assert row.is_confirmed and not row.is_dismissed and row.role == "member"
    assert Membership._base_manager.filter(group=roster.group, party=roster.people["member"]).count() == 1
