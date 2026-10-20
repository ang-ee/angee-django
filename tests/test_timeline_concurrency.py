"""PostgreSQL serializes projection and owner writes on the target row."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event as Latch
from time import monotonic

import pytest
from django.db import connection, connections

from angee.decisions.testing.models import Decision
from angee.decisions.timeline import DecisionKind
from angee.timeline.testing.models import Event
from tests.test_timeline_decisions import ask
from tests.test_timeline_decisions import people as people  # noqa: F401 -- shared manager fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_projection_locks_its_target_before_reading_committed_state(people, settings, monkeypatch) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock acceptance test")
    settings.ANGEE_TIMELINE_KINDS = ["angee.decisions.timeline.timeline_kinds"]
    entered, release, writing = Latch(), Latch(), Latch()
    target = {}
    original = DecisionKind.events

    def held_projection(self, row):
        if not entered.is_set():
            target["pk"] = row.pk
            entered.set()
            assert release.wait(10)
        return original(self, row)

    def first_write():
        try:
            return ask(people)
        finally:
            connections.close_all()

    def second_write():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                target["writer"] = cursor.fetchone()[0]
            writing.set()
            return Decision.objects.decide(Decision(pk=target["pk"]), actor=people[1], chosen=["complete"])
        finally:
            connections.close_all()

    monkeypatch.setattr(DecisionKind, "events", held_projection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_write)
        try:
            assert entered.wait(10)
            second = pool.submit(second_write)
            assert writing.wait(10)
            deadline = monotonic() + 5
            blockers: list[int] = []
            while monotonic() < deadline and not blockers:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [target["writer"]])
                    blockers = cursor.fetchone()[0]
                if not blockers:
                    release.wait(0.01)
            assert blockers, "The owner write must wait for the projection's target lock."
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert Event.objects.for_record(people[3]).get(kind="decisions.decision").detail == "Complete"
