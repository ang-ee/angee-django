"""A live timeline read never locks its source rows."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection, connections, transaction

from angee.decisions.testing.models import Decision
from tests.test_timeline import timeline_entries
from tests.test_timeline_decisions import ask
from tests.test_timeline_decisions import people as people  # noqa: F401 -- shared fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_union_reads_committed_state_while_a_writer_holds_the_source(people, settings):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL MVCC acceptance test")
    settings.ANGEE_TIMELINE_KINDS = ["angee.decisions.timeline.timeline_kinds"]
    question = ask(people)
    held, release = Event(), Event()

    def answer():
        try:
            with transaction.atomic():
                Decision.objects.decide(question, actor=people[1], chosen=["complete"])
                held.set()
                assert release.wait(10)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(answer)
        try:
            assert held.wait(10)
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '3s'")
            assert timeline_entries(people[3], people[1])[0].detail.startswith("Waiting")
        finally:
            release.set()
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = 0")
        writer.result(timeout=10)
    assert timeline_entries(people[3], people[1])[0].detail == "Complete"
