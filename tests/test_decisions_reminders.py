"""A question's due and reminder instants are the asker's; the reminder is stamped and announced once."""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from pydantic import ValidationError as ContractError
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.autoconfig import SETTINGS
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.signals import decision_reminder_due
from angee.decisions.tasks import remind_due
from angee.decisions.testing.models import Decision
from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import addon_schema, create_user, vault_for


@pytest.fixture
def people(db):
    requester, reviewer = (create_user(name) for name in ("rm-requester", "rm-reviewer"))
    subject = vault_for(requester, name="Catering order")
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    return requester, reviewer, subject


def request(subject, *assignees, **instants):
    return DecisionRequest(
        kind="order_review",
        records=(subject,),
        assignees=assignees,
        proposal=DecisionProposal(alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "confirmed"}]),
        **instants,
    )


@pytest.fixture
def reminders():
    received = []

    def collect(sender, decision, **kwargs):
        received.append(decision.pk)

    decision_reminder_due.connect(collect, weak=False)
    try:
        yield received
    finally:
        decision_reminder_due.disconnect(collect)


def sweep(now):
    with system_context(reason="tests.decisions.remind_due"):
        return Decision.objects.remind_due(now=now)


def test_the_asker_supplies_aware_due_and_reminder_instants(people):
    requester, reviewer, subject = people
    due, remind = datetime(2027, 3, 1, 9, tzinfo=UTC), datetime(2027, 2, 27, 9, tzinfo=UTC)
    question = Decision.objects.ask(request(subject, reviewer, due_at=due, remind_after=remind), actor=requester)
    question.refresh_from_db()
    assert (question.due_at, question.remind_after, question.reminded_at) == (due, remind, None)
    with pytest.raises(ContractError, match="timezone"):
        request(subject, reviewer, due_at=datetime(2027, 3, 1, 9))


def test_the_sweep_reminds_each_due_open_question_once(people, reminders):
    requester, reviewer, subject = people
    now = timezone.now()
    due = Decision.objects.ask(request(subject, reviewer, remind_after=now - timedelta(minutes=1)), actor=requester)
    later = Decision.objects.ask(request(subject, reviewer, remind_after=now + timedelta(hours=1)), actor=requester)
    unset = Decision.objects.ask(request(subject, reviewer), actor=requester)
    answered = Decision.objects.ask(request(subject, reviewer, remind_after=now - timedelta(hours=1)), actor=requester)
    answered.decide(actor=reviewer, chosen=["confirm"])
    assert sweep(now) == 1
    assert reminders == [due.pk]
    stamped = Decision.system_queryset().get(pk=due.pk)
    assert stamped.reminded_at is not None and stamped.revision == due.revision
    assert sweep(now + timedelta(minutes=5)) == 0
    assert reminders == [due.pk]
    assert not Decision.system_queryset().filter(pk__in=(later.pk, unset.pk, answered.pk), reminded_at__isnull=False)


def test_a_repeated_reminder_writes_and_sends_nothing(people, reminders):
    requester, reviewer, subject = people
    question = Decision.objects.ask(request(subject, reviewer), actor=requester)
    with system_context(reason="tests.decisions.remind"):
        first = question.remind().reminded_at
        assert question.remind().reminded_at == first
    assert reminders == [question.pk]
    with pytest.raises(PermissionDenied, match="system context"):
        question.remind()


def test_the_periodic_task_runs_the_sweep_at_its_instant(people, reminders):
    requester, reviewer, subject = people
    question = Decision.objects.ask(
        request(subject, reviewer, remind_after=datetime(2027, 1, 1, tzinfo=UTC)), actor=requester
    )
    assert remind_due(timestamp=int(datetime(2026, 12, 31, tzinfo=UTC).timestamp())) == 0
    assert remind_due(timestamp=int(datetime(2027, 1, 2, tzinfo=UTC).timestamp())) == 1
    assert reminders == [question.pk]
    entry = SETTINGS["CELERY_BEAT_SCHEDULE:append"]["decisions.remind_due"]
    assert entry["task"] == remind_due.name == "decisions.remind_due"


def test_the_sweep_leaves_a_workflow_held_question_to_its_step(execution, register_step, reminders):
    actor, _sent = execution
    subject = vault_for(actor, name="Workflow order")

    class OrderReview(DecisionStep[None, None, None]):
        key = "order_review"
        outcomes = {"confirmed": "Confirmed"}

        def ask(self, ctx):
            return ctx.ask(request(subject, remind_after=timezone.now() - timedelta(minutes=1)))

    register_step(OrderReview)
    workflow = load_workflow(
        {
            "nodes": {"review": {"step": OrderReview.key}},
            "results": [{"from": "review", "when": ["confirmed"], "as": "confirmed"}],
        },
        actor=actor,
    )
    run = start_run(workflow, actor=actor, subject=subject)
    run_until(run)
    held = StepRun.system_queryset().get(run=run).decision_id
    assert held is not None
    assert sweep(timezone.now()) == 0 and reminders == []


def test_overdue_is_derived_when_read(people):
    requester, reviewer, subject = people
    now = timezone.now()
    overdue = Decision.objects.ask(request(subject, reviewer, due_at=now - timedelta(minutes=1)), actor=requester)
    Decision.objects.ask(request(subject, reviewer, due_at=now + timedelta(days=1)), actor=requester)
    Decision.objects.ask(request(subject, reviewer), actor=requester)
    answered = Decision.objects.ask(request(subject, reviewer, due_at=now - timedelta(days=1)), actor=requester)
    answered.decide(actor=reviewer, chosen=["confirm"])
    schema = addon_schema(decision_schema.schemas, "console")
    rows = result_data(
        execute_schema(
            schema, "{ decisions(where: {is_overdue: {_eq: true}}) { id due_at reminded_at } }", user=reviewer
        )
    )["decisions"]
    assert [row["id"] for row in rows] == [overdue.sqid] and rows[0]["due_at"]
