"""Structural occurrence probes prune absent record branches before authorization."""

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import PermissionAuditEvent

from angee.base.testing.queries import statement_startswith
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.decisions.timeline import DecisionKind
from angee.nexus.timeline import CorrespondenceKind
from angee.projects.timeline import TaskKind
from angee.provenance.context import changeset
from angee.provenance.timeline import ChangeKind
from angee.timeline.reading import Timeline
from angee.workflows.states import RunStatus
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import WorkflowRun
from angee.workflows.timeline import RunKind
from tests.conftest import create_platform_admin, create_user, vault_for
from tests.test_timeline_nexus import correspondence as correspondence  # noqa: F401 -- shared corpus
from tests.test_workflows_review import review as review  # noqa: F401 -- real decision/run fixture
from tests.test_workflows_review import start_review
from tests.workflow_steps import document

pytestmark = pytest.mark.django_db(transaction=True)


def assert_occurs(kind, record, expected, actor):
    """A warm record predicate costs one SELECT, even under an unrelated actor."""
    kind.occurs(record)  # Warm native content-type identities.
    with actor_context(actor or AnonymousUser()), CaptureQueriesContext(connection) as queries:
        assert kind.occurs(record) is expected
    assert len(queries) == 1, [query["sql"] for query in queries]
    sql = queries[0]["sql"]
    assert statement_startswith(sql, "SELECT 1 ") and "LIMIT 1" in sql
    assert "rebac_" not in sql.lower()


def assert_branch_omitted(kind, record, actor, source_model, monkeypatch):
    """Keep a history branch so absence is checked in an actual page statement."""
    with system_context(reason="test.timeline.occurrence_history"), changeset(fresh=True):
        record.save()

    def forbidden(*args, **kwargs):
        pytest.fail(f"Absent {kind.id} compiled a scoped queryset")

    monkeypatch.setattr(type(kind), "queryset", forbidden)
    with override_settings(ANGEE_TIMELINE_KINDS=[
        "angee.provenance.timeline.timeline_kinds", f"{type(kind).__module__}.timeline_kinds",
    ]):
        with CaptureQueriesContext(connection) as queries:
            timeline = Timeline(record, actor)
            page = timeline.page()
        sql, = (query["sql"] for query in queries if '"timeline_source"' in query["sql"])
        assert f'"{source_model._meta.db_table}"' not in sql
        assert kind.id not in timeline.sources
        assert next(summary.count for summary in page.kinds if summary.id == kind.id) == 0
        assert all(entry.kind != kind.id and not entry.children for entry in page.entries)
        assert page.needs_me == timeline.attention() == 0


@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("kind, model_label", [
    (RunKind(), "workflows.WorkflowRun"), (DecisionKind(), "decisions.Decision"),
])
def test_unrelated_runs_and_decisions_do_not_compile_branches(review, kind, model_label, monkeypatch):
    owner, reviewers, _, question = review
    run, _step = start_review(review)
    absent = vault_for(create_user("unrelated-timeline-owner"))
    assert_occurs(kind, question.review_subject, True, reviewers[0])
    assert_occurs(kind, absent, False, reviewers[0])
    if isinstance(kind, DecisionKind):
        # Requester membership counts even though the decision concerns the vault.
        assert_occurs(kind, run, True, reviewers[0])
    assert_branch_omitted(kind, absent, owner, apps.get_model(model_label), monkeypatch)


@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("kind", [RunKind(), DecisionKind()])
def test_structural_occurrences_do_not_admit_unreadable_rows(review, kind, settings):
    _owner, _reviewers, _, question = review
    start_review(review)
    outsider = create_user("unreadable-timeline-occurrences")
    settings.ANGEE_TIMELINE_KINDS = [f"{type(kind).__module__}.timeline_kinds"]
    assert_occurs(kind, question.review_subject, True, outsider)
    with actor_context(outsider):
        timeline = Timeline(question.review_subject, outsider)
        page = timeline.page()
        assert page.entries == [] and page.needs_me == 0
        assert all(summary.count == 0 for summary in page.kinds)
        assert timeline.counts == {} and timeline.attention() == 0


def assert_attention_omitted(kind, record, actor, monkeypatch):
    """A present branch with no candidates never builds decision act authorization."""
    assert kind.occurs(record)  # Warm native content-type identities.

    def forbidden(*args, **kwargs):
        pytest.fail(f"{kind.id} built decision attention without a candidate on this record")

    monkeypatch.setattr(type(Decision.objects), "waiting_on", forbidden)
    before = PermissionAuditEvent.objects.count()
    with actor_context(AnonymousUser()), CaptureQueriesContext(connection) as queries:
        assert kind.waiting_on(record, actor) is None
    assert len(queries) == 1
    sql = queries[0]["sql"]
    assert statement_startswith(sql, "SELECT 1 ") and "LIMIT 1" in sql and "rebac_" not in sql.lower()
    assert PermissionAuditEvent.objects.count() == before
    with override_settings(ANGEE_TIMELINE_KINDS=[f"{type(kind).__module__}.timeline_kinds"]):
        timeline = Timeline(record, actor)
        page = timeline.page()
        assert len(page.entries) == 1 and not page.entries[0].needs_me
        assert timeline.counts == {kind.id: (1, 0)}
        assert page.needs_me == timeline.attention() == Timeline(record, actor).attention() == 0
        assert timeline.page(needs_me=True).entries == []


@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.WAITING])
def test_runs_without_decision_waits_on_this_record_skip_act_scope(review, status, monkeypatch):
    owner, _, _, _ = review
    start_review(review)  # A decision wait elsewhere must not admit this record's attention arm.
    record = vault_for(owner, name="No decision waits")
    workflow = load_workflow(
        document("entry", step="pause" if status == RunStatus.WAITING else "echo"),
        key="timeline-no-decision-waits", actor=owner,
    )
    run = WorkflowRun.objects.start(workflow, actor=owner, subject=record)
    if status != RunStatus.RUNNING:
        run_until(run)
    assert run.status == status
    assert_attention_omitted(RunKind(), record, owner, monkeypatch)


@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("closed", ["answered", "withdrawn"])
@pytest.mark.parametrize("membership", ["concern", "requester"])
def test_closed_decisions_on_this_record_skip_act_scope(review, closed, membership, monkeypatch):
    owner, reviewers, _, question = review
    run, step = start_review(review)
    if closed == "answered":
        step.decision.decide(actor=reviewers[0], chosen=["reject"])
    else:
        with system_context(reason="test.timeline.closed_decision"):
            step.decision.withdraw(actor=owner)
    # An open question elsewhere must not admit this record's attention arm.
    Decision.objects.ask(DecisionRequest(
        kind="unrelated", records=(vault_for(owner, name="Other questions"),), assignees=(owner,),
        proposal=DecisionProposal(alternatives=[{"key": "done", "label": "Done", "outcome": "done"}]),
    ), actor=owner)
    record = question.review_subject if membership == "concern" else run
    assert_attention_omitted(DecisionKind(), record, owner, monkeypatch)


@pytest.mark.usefixtures("workflow_step_classes", "activity_catalog", "composed_permissions")
def test_attention_population_writes_no_permission_audit_event(review, settings):
    """The page statement and standalone attention avoid audits, before payload hydration."""
    owner, reviewers, _, question = review
    run, _step = start_review(review)
    write_relationships([RelationshipTuple(to_object_ref(run), "reader", to_subject_ref(reviewers[0]))])
    task = apps.get_model("projects", "Task")
    with actor_context(owner):
        task.objects.schedule(question.review_subject.with_actor(owner), title="Attention", assignee=reviewers[0])
    settings.ANGEE_TIMELINE_KINDS = [
        "angee.workflows.timeline.timeline_kinds", "angee.decisions.timeline.timeline_kinds",
        "angee.projects.timeline.timeline_kinds",
    ]
    before = PermissionAuditEvent.objects.count()
    with actor_context(reviewers[0]), CaptureQueriesContext(connection) as queries:
        timeline = Timeline(question.review_subject, reviewers[0])
        selected, children = timeline._read(kinds=None, needs_me=False, anchor=None, limit=25)
        assert {row["_result_kind"] for row in selected} == {"workflows.run", "projects.task"}
        assert all(row["_result_needs"] for row in selected)
        assert any(child.kind == "decisions.decision" for counts in children.values() for child in counts)
        assert timeline.attention() == 2
        assert Timeline(question.review_subject, reviewers[0]).attention() == 2
    assert PermissionAuditEvent.objects.count() == before
    assert not any(statement_startswith(query["sql"], "INSERT ") for query in queries)


@pytest.mark.usefixtures("activity_catalog")
def test_unrelated_tasks_do_not_compile_a_branch(composed_permissions, monkeypatch):
    owner = create_platform_admin("task-occurrence-owner")
    present = vault_for(owner)
    absent = vault_for(create_user("task-occurrence-other"))
    task = apps.get_model("projects", "Task")
    with actor_context(owner):
        task.objects.schedule(present.with_actor(owner), title="Only on this record")
    kind = TaskKind()
    assert_occurs(kind, present, True, None)
    assert_occurs(kind, absent, False, None)
    assert_branch_omitted(kind, absent, owner, task, monkeypatch)


def test_history_probe_requires_closed_membership_on_this_record(composed_permissions, settings, monkeypatch):
    owner = create_platform_admin("history-occurrence-owner")
    with changeset(fresh=True):
        present = vault_for(owner)
    kind = ChangeKind()
    assert_occurs(kind, present, True, None)
    # The new record has only its still-open changeset; other closed changesets exist.
    with changeset(fresh=True):
        absent = vault_for(create_user("history-occurrence-other"))
        assert_occurs(kind, absent, False, None)
        settings.ANGEE_TIMELINE_KINDS = ["angee.provenance.timeline.timeline_kinds"]
        def forbidden(*args, **kwargs):
            pytest.fail("An open changeset compiled the history branch")
        monkeypatch.setattr(ChangeKind, "queryset", forbidden)
        timeline = Timeline(absent, owner)
        assert timeline.source is None
        assert timeline.page().entries == [] and timeline.attention() == 0


def test_correspondence_probe_matches_parties_and_confirmed_subtrees(correspondence, monkeypatch):
    data = correspondence
    kind = CorrespondenceKind()
    for record in (data.party, data.circle, data.parent):
        assert_occurs(kind, record, True, None)
    assert_occurs(kind, data.other, False, None)
    assert_branch_omitted(kind, data.other, data.actor, type(data.message), monkeypatch)
    type(data.member)._base_manager.filter(pk=data.member.pk).update(is_dismissed=True)
    for record in (data.circle, data.parent):
        assert_occurs(kind, record, False, None)
    type(data.message)._base_manager.filter(pk=data.message.pk).update(is_trashed=True)
    assert_occurs(kind, data.party, False, None)
