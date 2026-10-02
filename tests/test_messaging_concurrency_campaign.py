"""PostgreSQL notification acknowledgement locks and concurrent first receipt."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.messaging.testing.models import ThreadNotification
from tests.messaging_campaign import add_member, fanout
from tests.messaging_campaign import audience_record as audience_record
from tests.test_spaces import spaces_tables as spaces_tables

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="PostgreSQL notification row-lock and two-writer contract",
    ),
]


def test_notification_acknowledgement_locks_the_recipient_scoped_row(audience_record):
    case = audience_record
    add_member(case)
    notice = ThreadNotification._base_manager.get(message=fanout(case))
    with actor_context(case.recipient), CaptureQueriesContext(connection) as captured:
        ThreadNotification.objects.mark_read(notice, case.recipient)
    locks = [query["sql"] for query in captured
             if "FOR UPDATE" in query["sql"] and ThreadNotification._meta.db_table in query["sql"]]
    assert len(locks) == 1


def test_two_acknowledgers_return_the_same_first_read_timestamp(audience_record):
    case = audience_record
    add_member(case)
    notice = ThreadNotification._base_manager.get(message=fanout(case))
    ready = Barrier(2)

    def acknowledge():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '5s'")
            ready.wait(timeout=5)
            with actor_context(case.recipient):
                return ThreadNotification.objects.mark_read(notice, case.recipient).read_at
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(acknowledge) for _ in range(2)]
        timestamps = [result.result(timeout=15) for result in results]
    notice.refresh_from_db()
    assert timestamps[0] == timestamps[1] == notice.read_at
    assert notice.read_at is not None
