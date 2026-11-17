"""PostgreSQL first-answer, replacement and reminder concurrency through independent connections."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models.locks import StaleRevisionError
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.signals import decision_reminder_due
from angee.decisions.testing.drivers import seed_decision
from angee.decisions.testing.models import Decision
from tests.conftest import create_user, vault_for
from tests.lock_waits import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.fixture
def people():
    requester, reviewer, lead = (create_user(f"concurrent-{role}") for role in ("requester", "reviewer", "lead"))
    subject = vault_for(requester)
    write_relationships(
        [RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(person)) for person in (reviewer, lead)]
    )
    return requester, reviewer, lead, subject


def request(subject, requester, *assignees, **instants):
    return DecisionRequest(
        kind="concurrent_review",
        records=(subject,),
        assignees=assignees,
        requester=requester,
        proposal=DecisionProposal(alternatives=[{"key": "accept", "label": "Accept", "outcome": "accepted"}]),
        **instants,
    )


def supersede(question, replacement, *, actor, reason):
    with system_context(reason="test.supersede_race"):
        return Decision.objects.supersede(question, replacement, actor=actor, reason=reason)


def test_competing_assignees_cannot_replace_the_first_answer(people):
    requester, reviewer, _lead, subject = people
    decision = seed_decision(actor=requester, assignees=(requester, reviewer), reference=subject, requester=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.answer_race"):
            Decision.system_queryset().select_for_update().get(pk=decision.pk)
            contender, pid = submit(
                pool,
                lambda: decision.decide(actor=reviewer, chosen=["reject"], revision=decision.revision),
            )
            wait_for_lock(pid, contender)
            decision.decide(actor=requester, chosen=["accept"], revision=decision.revision)
        with pytest.raises(StaleRevisionError):
            contender.result(timeout=10)
    retained = Decision.system_queryset().get(pk=decision.pk)
    assert retained.verdict == ["accept"] and retained.answered_by_id == requester.pk


def test_two_supersedes_of_one_question_leave_one_replacement(people):
    requester, reviewer, lead, subject = people
    question = Decision.objects.ask(request(subject, requester, reviewer), actor=requester)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.supersede_race.holder"):
            Decision.system_queryset().select_for_update().get(pk=question.pk)
            contender, pid = submit(
                pool,
                lambda: supersede(question, request(subject, requester, lead), actor=requester, reason="Second"),
            )
            wait_for_lock(pid, contender)
            winner = Decision.objects.supersede(
                question, request(subject, requester, lead), actor=requester, reason="First"
            )
        with pytest.raises(ValidationError, match="reload"):
            contender.result(timeout=10)
    replaced = Decision.system_queryset().get(pk=question.pk)
    assert (replaced.superseded_by_id, replaced.withdrawal_reason) == (winner.pk, "First")
    assert list(Decision.system_queryset().open().values_list("pk", flat=True)) == [winner.pk]


def test_a_held_answer_refuses_the_waiting_supersede(people):
    requester, reviewer, lead, subject = people
    question = Decision.objects.ask(request(subject, requester, reviewer), actor=requester)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.answer_supersede_race"):
            Decision.system_queryset().select_for_update().get(pk=question.pk)
            contender, pid = submit(
                pool,
                lambda: supersede(question, request(subject, requester, lead), actor=requester, reason="Escalated"),
            )
            wait_for_lock(pid, contender)
            question.decide(actor=reviewer, chosen=["accept"], revision=question.revision)
        with pytest.raises(ValidationError, match="reload"):
            contender.result(timeout=10)
    assert Decision.system_queryset().get(pk=question.pk).verdict == ["accept"]
    assert Decision.system_queryset().count() == 1


def test_a_held_supersede_refuses_the_waiting_answer(people):
    requester, reviewer, lead, subject = people
    question = Decision.objects.ask(request(subject, requester, reviewer), actor=requester)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.supersede_answer_race"):
            Decision.system_queryset().select_for_update().get(pk=question.pk)
            contender, pid = submit(
                pool,
                lambda: question.decide(actor=reviewer, chosen=["accept"], revision=question.revision),
            )
            wait_for_lock(pid, contender)
            replacement = Decision.objects.supersede(
                question, request(subject, requester, lead), actor=requester, reason="Escalated"
            )
        with pytest.raises(StaleRevisionError):
            contender.result(timeout=10)
    replaced = Decision.system_queryset().get(pk=question.pk)
    assert replaced.verdict == [] and replaced.superseded_by_id == replacement.pk


def test_two_sweeps_send_one_reminder(people):
    requester, reviewer, _lead, subject = people
    now = timezone.now()
    question = Decision.objects.ask(
        request(subject, requester, reviewer, remind_after=now - timedelta(minutes=1)), actor=requester
    )
    sent = []

    def collect(sender, decision, **kwargs):
        sent.append(decision.pk)

    decision_reminder_due.connect(collect, weak=False)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                assert Decision.objects.remind_due(now=now) == 1
                # The contender skips the row this sweep still holds instead of waiting on it.
                contender, _pid = submit(pool, lambda: Decision.objects.remind_due(now=now))
                assert contender.result(timeout=10) == 0
            assert Decision.objects.remind_due(now=now) == 0
    finally:
        decision_reminder_due.disconnect(collect)
    assert sent == [question.pk]
    assert Decision.system_queryset().get(pk=question.pk).reminded_at is not None
