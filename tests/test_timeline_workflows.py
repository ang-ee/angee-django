"""Runs and waiting decisions update the ledger through real execution."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.changesets import changeset
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.publishing import change_published
from angee.graphql.schema import GraphQLSchemas
from angee.identity import public_id_of
from angee.signals import bulk_updated
from angee.timeline.testing.models import Event
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.states import RunStatus, WaitingKind
from angee.workflows.testing.drivers import decide, load_workflow, run_until
from angee.workflows.testing.models import StepRun, WorkflowRun
from tests.conftest import create_user
from tests.mtidemo.models import MtiChild, MtiParent
from tests.test_timeline import read_timeline
from tests.test_workflows_review import review as review  # noqa: F401 -- shared real review driver
from tests.test_workflows_review import start_review
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = [
        "angee.workflows.timeline.timeline_kinds", "angee.decisions.timeline.timeline_kinds",
        "angee.provenance.timeline.timeline_kinds",
    ]


def test_run_follows_execution_without_an_explicit_projection(execution) -> None:
    actor, _sent = execution
    subject = create_user("timeline-run-subject")
    workflow = load_workflow(
        document("entry"), key="timeline-run", actor=actor, subject_model=actor._meta.label, name="Timeline run",
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    event = Event.objects.for_record(subject).get(kind="workflows.run")
    assert (event.target_model, event.target_pk) == ("workflows.WorkflowRun", run.pk)
    assert event.title == "Timeline run" and event.detail == "Running"
    assert event.fold_pk is None
    assert not Event.objects.for_record(run).filter(target_model="workflows.WorkflowRun", target_pk=run.pk).exists()
    run_until(run)
    run.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    event.refresh_from_db()
    assert event.detail == "Succeeded"
    assert Event.objects.for_record(subject).filter(kind="workflows.run").count() == 1
    # Reading the subject is insufficient to see a run acting on it.
    data = read_timeline(subject, subject, kinds=["workflows.run"])
    assert data["timeline"]["entries"] == []
    assert next(kind["count"] for kind in data["timeline"]["kinds"] if kind["id"] == "workflows.run") == 0


def test_waiting_run_folds_readable_children_and_computes_attention(review) -> None:
    actor, reviewers, _, question = review
    batches = []

    def observed(sender, *, instances, **kwargs):
        batches.append(instances)

    bulk_updated.connect(observed, sender=StepRun)
    try:
        run, step = start_review(review)
    finally:
        bulk_updated.disconnect(observed, sender=StepRun)
    assert len(batches) == 1
    reviewer = reviewers[0]
    subject = question.review_subject
    with system_context(reason="test.timeline.run_reader"):
        write_relationships([RelationshipTuple(to_object_ref(run), "reader", to_subject_ref(reviewer))])
    decision_event = Event.objects.for_record(subject).get(kind="decisions.decision", target_pk=step.decision_id)
    assert (decision_event.fold_model, decision_event.fold_pk) == ("workflows.WorkflowRun", run.pk)
    assert Event.objects.for_record(run).get(kind="decisions.decision").fold_pk is None
    with system_context(reason="test.timeline.run_change"), changeset(fresh=True, origin=run):
        subject.name = "A run edit"
        subject.save(update_fields=["name"])

    with CaptureQueriesContext(connection) as queries:
        data = read_timeline(subject, reviewer, kinds=["workflows.run"], needsMe=True)
    (line,) = data["timeline"]["entries"]
    assert line["target_id"] == public_id_of(run) and line["needs_me"]
    assert {"kind": "decisions.decision", "count": 1} in line["children"]
    assert {"kind": "provenance.change", "count": 1} in line["children"]
    assert data["timeline_attention"] == data["timeline"]["needs_me"] == 1
    folded_groups = [
        query["sql"] for query in queries.captured_queries
        if '"fold_model" AS "fold_model"' in query["sql"] and "GROUP BY" in query["sql"]
    ]
    assert len(folded_groups) == 1

    bystander = create_user("timeline-fold-bystander")
    with system_context(reason="test.timeline.fold_reader"):
        write_relationships([
            RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(bystander)),
            RelationshipTuple(to_object_ref(run), "reader", to_subject_ref(bystander)),
        ])
    hidden = read_timeline(subject, bystander, kinds=["workflows.run"])
    (readable_run,) = hidden["timeline"]["entries"]
    assert not readable_run["needs_me"]
    assert all(child["kind"] != "decisions.decision" for child in readable_run["children"])
    assert hidden["timeline_attention"] == hidden["timeline"]["needs_me"] == 0

    # Folding is a target reference: deleting a projection never cascades into its children.
    Event.objects.for_record(subject).filter(kind="workflows.run").delete()
    assert Event.objects.filter(pk=decision_event.pk).exists()
    decide(step.decision, actor=reviewer, chosen=["approve"])
    run_until(run)
    assert Event.objects.for_record(subject).get(kind="workflows.run").detail == "Succeeded"
    assert read_timeline(subject, reviewer)["timeline_attention"] == 0


def test_unreadable_or_missing_run_never_hides_a_readable_question_or_change(review) -> None:
    _, reviewers, _, question = review
    run, step = start_review(review)
    subject, reviewer = question.review_subject, reviewers[0]
    with system_context(reason="test.timeline.hidden_run"), changeset(
        fresh=True, origin=run, change_reason="Private run edit",
    ):
        subject.name = "Changed by a private run"
        subject.save(update_fields=["name"])
    data = read_timeline(subject, reviewer)
    assert not any(line["kind"] == "workflows.run" for line in data["timeline"]["entries"])
    assert any(line["kind"] == "provenance.change" and line["detail"] == "Private run edit"
               for line in data["timeline"]["entries"])
    decision = next(line for line in data["timeline"]["entries"] if line["kind"] == "decisions.decision")
    assert decision["needs_me"] and decision["children"] == []
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 1
    assert next(kind["count"] for kind in data["timeline"]["kinds"] if kind["id"] == "decisions.decision") == 1
    decide(step.decision, actor=reviewer, chosen=["reject"])
    run_until(run)
    Event.objects.filter(kind="workflows.run", target_pk=run.pk).delete()
    data = read_timeline(subject, reviewer, kinds=["decisions.decision"])
    assert len(data["timeline"]["entries"]) == 1
    assert not data["timeline"]["entries"][0]["needs_me"]


def test_later_step_decision_link_reprojects_the_question_and_claims_are_silent(review) -> None:
    actor, reviewers, _, question = review
    subject = question.review_subject
    workflow = load_workflow(document("entry"), key="late-question", actor=actor, subject_model=subject._meta.label)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    decision = Decision.objects.ask(DecisionRequest(
        kind="later", records=(subject,), assignees=reviewers,
        proposal=DecisionProposal.model_validate({
            "alternatives": [{"key": "done", "label": "Done", "outcome": "done"}],
        }),
    ), actor=actor)
    event = Event.objects.for_record(subject).get(kind="decisions.decision", target_pk=decision.pk)
    assert event.fold_pk is None
    published, batches = [], []

    def observed(sender, **kwargs):
        published.append(sender)

    def batch(sender, *, instances, **kwargs):
        batches.append(instances)

    change_published.connect(observed)
    bulk_updated.connect(batch, sender=StepRun)
    try:
        with system_context(reason="test.timeline.late_question"):
            rows = StepRun.objects.filter(run=run)
            rows.to_running(timedelta(seconds=30), until=timezone.now() + timedelta(seconds=30))
            assert published == []
            rows.to_waiting(kind=WaitingKind.DECISION, decision_id=decision.pk)
    finally:
        change_published.disconnect(observed)
        bulk_updated.disconnect(batch, sender=StepRun)
    assert len(batches) == 1
    assert all({"input", "output", "state", "notes"} <= row.get_deferred_fields() for row in batches[0])
    assert published.count(WorkflowRun) == 0  # Run advancement publishes its completed state separately.
    assert published.count(Decision) == 1
    event.refresh_from_db()
    assert (event.fold_model, event.fold_pk) == ("workflows.WorkflowRun", run.pk)


@pytest.mark.parametrize("assigned", [True, False])
def test_each_committed_workflow_wait_refreshes_the_inherited_record_feed(
    execution, register_step, monkeypatch, assigned,
):
    """Like the AP page: a canonical ledger record has only a child's public feed."""

    actor, _sent = execution
    with system_context(reason="test.timeline.inherited_subject"):
        subject = MtiChild.objects.create(title="Review twice")
        write_relationships([
            RelationshipTuple(to_object_ref(subject), "reader", to_subject_ref(actor)),
            RelationshipTuple(to_object_ref(MtiParent.objects.get(pk=subject.pk)), "reader", to_subject_ref(actor)),
        ])

    class Question(DecisionStep[None, None, None]):
        key = "timeline_commit_question"
        outcomes = {"done": "Continue"}

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind="review", records=(subject,), assignees=(actor,) if assigned else (),
                proposal=DecisionProposal(alternatives=[{"key": "done", "label": "Continue", "outcome": "done"}]),
            ))

    register_step(Question)
    monkeypatch.setattr(
        GraphQLSchemas, "change_publisher_model_labels", lambda self: frozenset({MtiChild._meta.label_lower}),
    )
    snapshots = []

    def broadcast(model, payload):
        if model is MtiChild:
            assert not connection.in_atomic_block
            assert payload["id"] == public_id_of(subject)
            assert payload["resource_id"] == str(subject.pk)
            assert payload["related_records"] == [{"model": MtiChild._meta.label, "id": public_id_of(subject)}]
            # Read exactly when a subscriber receives the committed invalidation.
            snapshots.append(read_timeline(subject, actor))

    monkeypatch.setattr("angee.graphql.publishing._broadcast", broadcast)
    workflow = load_workflow({
        "nodes": {
            "first": {"step": Question.key, "label": "First review", "next": {"done": ["second"]}},
            "second": {"step": Question.key, "label": "Second review"},
        },
        "results": [{"from": "second", "when": ["done"], "as": "done"}],
    }, key="timeline-committed-waits", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    for node in ("first", "second"):
        snapshots.clear()
        run_until(run)
        step = StepRun.system_queryset().get(run=run, node_key=node)
        event = Event.objects.for_record(subject).get(kind="workflows.run", target_pk=run.pk)
        assert event.detail == f"Waiting · {step.node_label}"
        assert snapshots, "The declared child feed must refresh after the projection commits."
        page = snapshots[-1]
        assert page["timeline_attention"] == page["timeline"]["needs_me"] == 1
        (line,) = page["timeline"]["entries"]
        assert line["detail"] == event.detail and line["needs_me"]
        assert {"kind": "decisions.decision", "count": 1 if node == "first" else 2} in line["children"]
        decide(step.decision, actor=actor, chosen=["done"])
    run_until(run)
    assert snapshots[-1]["timeline_attention"] == 0
    assert snapshots[-1]["timeline"]["entries"][0]["detail"] == "Succeeded"
