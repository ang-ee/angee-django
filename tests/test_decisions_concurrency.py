"""PostgreSQL decision interleavings through real verbs and independent connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from time import monotonic, sleep

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Now
from django.utils import timezone
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.exceptions import RetryableDecisionError
from angee.decisions.forms import Action
from angee.decisions.signals import decision_group_settled
from angee.decisions.states import Verdict
from tests.conftest import create_user, vault_for
from tests.decisions_models import Decision, DecisionEvidence, DecisionGroup

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


class RecordNote(Action, key="record", label="Record", verdict=Verdict.COMPLETED):
    """A minimal durable answer for competing requests."""

    note: str


@pytest.fixture
def people(composed_tables):
    """Provide ordinary people with standing access to one document."""

    issuer, reviewer, other = (create_user(name) for name in ("issuer", "reviewer", "other"))
    subject = vault_for(issuer, name="Shared document")
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(person))
        for person in (reviewer, other)
    ])
    return issuer, reviewer, other, subject


def request_for(people, **changes):
    """Declare a seat whose participants already read the subject."""

    _issuer, reviewer, other, subject = people
    return DecisionRequest(**{
        "kind": "note_review", "subject": subject, "assignees": (reviewer, other),
        "actions": (RecordNote,), **changes,
    })


def seat(group, index=0):
    """Read one committed decision independently of any ambient principal."""

    return system_queryset(Decision).get(group=group, index=index)


def answer(decision, actor, note="Retained answer"):
    """Submit the revision the person actually observed."""

    return Decision.objects.decide(
        decision.pk, actor=actor, revision=decision.revision, action="record", values={"note": note},
    )


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


@pytest.mark.parametrize("first_holder", ["decide", "expiry"])
def test_t9_decide_races_with_expiry_in_both_lock_orders(people, first_holder):
    """A retained answer stays final; expiry blocks and rejects a stale deciding request."""

    issuer, reviewer, _other, _subject = people
    deadline = timezone.now() + timedelta(seconds=2)
    group = Decision.objects.admit_group([request_for(people, expires_at=deadline)], actor=issuer)
    decision = seat(group)
    if first_holder == "expiry":
        with system_context(reason="test.elapsed_decision_deadline"):
            Decision.objects.filter(pk=decision.pk).owner_update(expires_at=Now() - timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(group.pk):
            if first_holder == "decide":
                answer(decision, reviewer)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_sleep(GREATEST(EXTRACT(EPOCH FROM %s - clock_timestamp()), 0) + 0.05)",
                        [deadline],
                    )
                contender, _pid = submit(pool, Decision.objects.expire_due)
                assert contender.result(timeout=5) == 0
            else:
                assert Decision.objects.expire_due() == 1
                contender, pid = submit(pool, lambda: answer(decision, reviewer))
                wait_for_lock(pid, contender)
        if first_holder == "expiry":
            with pytest.raises(ValidationError, match="changed; reload"):
                contender.result(timeout=10)
    retained = seat(group)
    assert retained.revision == decision.revision + 1
    assert system_queryset(DecisionGroup).get(pk=group.pk).settled_at is not None
    assert retained.closed_reason == ("resolved" if first_holder == "decide" else "expired")
    assert retained.resolution == ({"action": "record", "note": "Retained answer"}
                                   if first_holder == "decide" else {})
    assert Decision.objects.expire_due() == 0


def test_two_people_deciding_the_same_seat_keep_the_first_answer(people):
    """A person's actual blocked request cannot replace the earlier answer."""

    issuer, reviewer, other, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(group.pk):
            contender, pid = submit(pool, lambda: answer(decision, other, "Later answer"))
            wait_for_lock(pid, contender)
            answer(decision, reviewer)
        with pytest.raises(ValidationError, match="changed; reload"):
            contender.result(timeout=10)
    retained = seat(group)
    assert retained.resolved_by_id == reviewer.pk and retained.revision == decision.revision + 1
    assert retained.resolution["note"] == "Retained answer"


@pytest.mark.parametrize("winner", ["decide", "cancel"])
def test_decide_races_with_cancel_in_both_lock_orders(people, winner):
    """Cancel closes only pending seats and a canceled seat cannot accept an answer."""

    issuer, reviewer, _other, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(group.pk):
            call = (lambda: Decision.objects.cancel_group(group.pk)) if winner == "decide" else (
                lambda: answer(decision, reviewer)
            )
            contender, pid = submit(pool, call)
            wait_for_lock(pid, contender)
            if winner == "decide":
                answer(decision, reviewer)
            else:
                assert Decision.objects.cancel_group(group.pk) == 1
        if winner == "decide":
            assert contender.result(timeout=10) == 0
        else:
            with pytest.raises(ValidationError, match="changed; reload"):
                contender.result(timeout=10)
    assert seat(group).closed_reason == ("resolved" if winner == "decide" else "canceled")


@pytest.mark.parametrize("winner", ["decide", "supersede"])
def test_decide_races_with_superseding_admission(people, winner):
    """Supersession preserves an answer or makes the blocked deciding request stale."""

    issuer, reviewer, _other, _subject = people
    request = request_for(people, supersede=True)
    old = Decision.objects.admit_group([request], actor=issuer)
    decision = seat(old)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(old.pk):
            if winner == "decide":
                contender, _pid = submit(pool, lambda: Decision.objects.admit_group([request], actor=issuer))
                with pytest.raises(RetryableDecisionError, match="busy"):
                    contender.result(timeout=5)
                answer(decision, reviewer)
            else:
                contender, pid = submit(pool, lambda: answer(decision, reviewer))
                wait_for_lock(pid, contender)
                new = Decision.objects.admit_group([request], actor=issuer)
        if winner == "supersede":
            with pytest.raises(ValidationError, match="changed; reload"):
                contender.result(timeout=10)
    if winner == "decide":
        new = Decision.objects.admit_group([request], actor=issuer)
        assert seat(old).closed_reason == "resolved"
    else:
        assert seat(old).closed_reason == "superseded" and seat(old).superseded_by_id == seat(new).pk
    assert seat(new).is_open


def test_last_two_all_policy_seats_settle_once_when_decided_concurrently(people):
    """Two simultaneous last answers settle their group exactly once after commit."""

    issuer, reviewer, other, _subject = people
    group = Decision.objects.admit_group([request_for(people), request_for(people)], actor=issuer, policy="all")
    first, last = seat(group), seat(group, 1)
    settled = []

    def received(sender, group, **kwargs):
        settled.append(group.pk)

    decision_group_settled.connect(received, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with DecisionGroup.objects.hold(group.pk):
                contender, pid = submit(pool, lambda: answer(last, other))
                wait_for_lock(pid, contender)
                answer(first, reviewer)
                assert system_queryset(DecisionGroup).get(pk=group.pk).settled_at is None
                assert settled == []
            assert contender.result(timeout=10).closed_reason == "resolved"
        assert settled == [group.pk]
    finally:
        decision_group_settled.disconnect(received)
    assert system_queryset(Decision).filter(group=group, closed_reason="resolved").count() == 2


@pytest.mark.parametrize("winner", ["expiry", "cancel"])
def test_expiry_races_with_cancel(people, winner):
    """The first closing transition remains final under competing cleanup requests."""

    issuer, _reviewer, _other, _subject = people
    group = Decision.objects.admit_group([
        request_for(people, expires_at=timezone.now() + timedelta(minutes=5)),
    ], actor=issuer)
    with system_context(reason="test.elapse_decision_deadline"):
        Decision.objects.filter(group=group).owner_update(expires_at=Now() - timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(group.pk):
            if winner == "expiry":
                contender, pid = submit(pool, lambda: Decision.objects.cancel_group(group.pk))
                wait_for_lock(pid, contender)
                assert Decision.objects.expire_due() == 1
            else:
                assert Decision.objects.cancel_group(group.pk) == 1
                contender, _pid = submit(pool, Decision.objects.expire_due)
                assert contender.result(timeout=5) == 0
        assert contender.result(timeout=10) == 0
    assert seat(group).closed_reason == ("expired" if winner == "expiry" else "canceled")


def test_empty_question_supersession_is_enforced_by_the_unique_constraint(people):
    """An invisible competing insert waits for the constraint and receives a retryable error."""

    issuer, _reviewer, _other, _subject = people
    request = request_for(people, supersede=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            first = Decision.objects.admit_group([request], actor=issuer)
            contender, pid = submit(pool, lambda: Decision.objects.admit_group([request], actor=issuer))
            wait_for_lock(pid, contender)
        with pytest.raises(RetryableDecisionError, match="retry"):
            contender.result(timeout=10)
    assert system_queryset(DecisionGroup).count() == 1 and seat(first).is_open
    replacement = Decision.objects.admit_group([request], actor=issuer)
    assert seat(first).superseded_by_id == seat(replacement).pk
    assert system_queryset(Decision).open().count() == 1


@pytest.mark.parametrize("winner", ["admission", "deletion"])
def test_evidence_deletion_races_with_admission(people, winner):
    """The target delete and evidence insert serialize without an orphan or silent deletion."""

    issuer, _reviewer, _other, record = people
    record_id = record.pk
    request = request_for(people, subject=None, context=DecisionContext(references=(
        DecisionRecordReference(model=record._meta.label, id=str(record.sqid)),
    )))

    def delete_record():
        with actor_context(issuer):
            type(record).objects.get(pk=record_id).delete()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            if winner == "admission":
                group = Decision.objects.admit_group([request], actor=issuer)
                contender, pid = submit(pool, delete_record)
            else:
                delete_record()
                contender, pid = submit(pool, lambda: Decision.objects.admit_group([request], actor=issuer))
            wait_for_lock(pid, contender)
        if winner == "admission":
            with pytest.raises(ProtectedError, match="decision evidence"):
                contender.result(timeout=10)
            assert system_queryset(type(record)).filter(pk=record_id).exists()
            assert system_queryset(DecisionEvidence).filter(decision__group=group).count() == 1
        else:
            with pytest.raises(ValidationError, match="[Ee]vidence|referenced record"):
                contender.result(timeout=10)
            assert not system_queryset(type(record)).filter(pk=record_id).exists()
            assert not system_queryset(DecisionGroup).exists()


def test_admission_waits_for_an_evidence_edit_without_becoming_a_conflict(people):
    """Saving a referenced document delays admission and never creates a busy error."""

    issuer, _reviewer, _other, record = people
    request = request_for(people, subject=None, context=DecisionContext(references=(
        DecisionRecordReference(model=record._meta.label, id=str(record.sqid)),
    )))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), actor_context(issuer):
            record.name = "Updated document"
            record.save(update_fields=("name",))
            contender, pid = submit(pool, lambda: Decision.objects.admit_group([request], actor=issuer))
            wait_for_lock(pid, contender)
        group = contender.result(timeout=10)
    assert seat(group).is_open


def test_group_deletion_waits_before_locking_any_of_its_decisions(people):
    """A waiter holding the group can still read its decision while deletion waits."""

    issuer, reviewer, _other, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = answer(seat(group), reviewer)

    def delete_group():
        with system_context(reason="test.delete_settled_group"):
            return system_queryset(DecisionGroup).get(pk=group.pk).delete()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with DecisionGroup.objects.hold(group.pk):
            contender, pid = submit(pool, delete_group)
            wait_for_lock(pid, contender)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '1000ms'")
            retained = Decision.objects.resolution(decision.pk, actor=issuer, actions=(RecordNote,))
            assert retained.decision.pk == decision.pk
        contender.result(timeout=10)
    assert not system_queryset(DecisionGroup).filter(pk=group.pk).exists()
    assert not system_queryset(Decision).filter(pk=decision.pk).exists()
