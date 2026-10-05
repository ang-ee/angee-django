"""PostgreSQL first-answer concurrency through independent connections."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection, connections, transaction
from rebac import system_context

from angee.base.mixins import StaleRevisionError
from angee.base.scoping import system_queryset
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision
from tests.conftest import create_user, vault_for

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def submit(pool, call):
    """Start an independent database connection and return its real backend pid."""

    started = Queue()

    def invoke():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                started.put(cursor.fetchone()[0])
            return call()
        finally:
            connections.close_all()

    future = pool.submit(invoke)
    return future, started.get(timeout=10)


def wait_for_lock(pid, future):
    """Prove a worker is blocked in PostgreSQL before releasing the held row."""

    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            state = cursor.fetchone()
        if state == ("Lock",):
            return
        if future.done():
            pytest.fail(f"Worker finished before waiting on a row lock: {future.result()!r}")
        sleep(0.01)
    pytest.fail(f"PostgreSQL backend {pid} did not report wait_event_type='Lock'.")


def test_competing_assignees_cannot_replace_the_first_answer(composed_tables):
    requester = create_user("concurrent-requester")
    reviewer = create_user("concurrent-reviewer")
    subject = vault_for(requester)
    from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    decision = seed_decision(actor=requester, assignees=(requester, reviewer), reference=subject, requester=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.answer_race"):
            system_queryset(Decision).select_for_update().get(pk=decision.pk)
            contender, pid = submit(
                pool,
                lambda: Decision.objects.decide(
                    decision, actor=reviewer, chosen=["reject"], revision=decision.revision
                ),
            )
            wait_for_lock(pid, contender)
            Decision.objects.decide(decision, actor=requester, chosen=["accept"], revision=decision.revision)
        with pytest.raises(StaleRevisionError):
            contender.result(timeout=10)
    retained = system_queryset(Decision).get(pk=decision.pk)
    assert retained.verdict == ["accept"] and retained.answered_by_id == requester.pk
