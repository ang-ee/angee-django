"""Structural occurrence probes prune absent record branches before authorization."""

import re

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import PermissionAuditEvent

from angee.base.refs import generic_pointer_target
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


def assert_occurs(kind, record, expected, actor):
    """A warm record predicate costs one SELECT, even under an unrelated actor."""
    kind.occurs(record)  # Warm native content-type identities.
    with actor_context(actor or AnonymousUser()), CaptureQueriesContext(connection) as queries:
        assert kind.occurs(record) is expected
    assert len(queries) == 1, [query["sql"] for query in queries]
    sql = queries[0]["sql"]
    assert statement_startswith(sql, "SELECT 1 ") and "LIMIT 1" in sql
    assert "rebac_" not in sql.lower()


def assert_branch_omitted(kind, record, actor, source_model):
    """Keep a history branch so absence is checked in an actual page statement."""
    with system_context(reason="test.timeline.occurrence_history"), changeset(fresh=True):
        record.save()

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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("kind, model_label", [
    (RunKind(), "workflows.WorkflowRun"), (DecisionKind(), "decisions.Decision"),
])
def test_unrelated_runs_and_decisions_do_not_compile_branches(review, kind, model_label):
    owner, reviewers, _, question = review
    run, _step = start_review(review)
    absent = vault_for(create_user("unrelated-timeline-owner"))
    assert_occurs(kind, question.review_subject, True, reviewers[0])
    assert_occurs(kind, absent, False, reviewers[0])
    if isinstance(kind, DecisionKind):
        # Requester membership counts even though the decision concerns the vault.
        assert_occurs(kind, run, True, reviewers[0])
    assert_branch_omitted(kind, absent, owner, apps.get_model(model_label))


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


def assert_attention_omitted(kind, record, actor):
    """A present branch with no candidates never builds decision act authorization."""
    assert kind.occurs(record)  # Warm native content-type identities.
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
def test_runs_without_decision_waits_on_this_record_skip_act_scope(review, status):
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
    assert_attention_omitted(RunKind(), record, owner)


@pytest.mark.usefixtures("workflow_step_classes")
@pytest.mark.parametrize("closed", ["answered", "withdrawn"])
@pytest.mark.parametrize("membership", ["concern", "requester"])
def test_closed_decisions_on_this_record_skip_act_scope(review, closed, membership):
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
    assert_attention_omitted(DecisionKind(), record, owner)


@pytest.mark.usefixtures("workflow_step_classes", "activity_catalog")
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("activity_catalog")
def test_unrelated_tasks_do_not_compile_a_branch():
    owner = create_platform_admin("task-occurrence-owner")
    present = vault_for(owner)
    absent = vault_for(create_user("task-occurrence-other"))
    task = apps.get_model("projects", "Task")
    with actor_context(owner):
        task.objects.schedule(present.with_actor(owner), title="Only on this record")
    kind = TaskKind()
    assert_occurs(kind, present, True, None)
    assert_occurs(kind, absent, False, None)
    assert_branch_omitted(kind, absent, owner, task)


@pytest.mark.django_db(transaction=True)
def test_history_probe_requires_closed_membership_on_this_record(settings, monkeypatch):
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


@pytest.mark.django_db(transaction=True)
def test_correspondence_probe_matches_parties_and_confirmed_subtrees(correspondence):
    data = correspondence
    kind = CorrespondenceKind()
    for record in (data.party, data.circle, data.parent):
        assert_occurs(kind, record, True, None)
    assert_occurs(kind, data.other, False, None)
    assert_branch_omitted(kind, data.other, data.actor, type(data.message))
    type(data.member)._base_manager.filter(pk=data.member.pk).update(is_dismissed=True)
    for record in (data.circle, data.parent):
        assert_occurs(kind, record, False, None)
    type(data.message)._base_manager.filter(pk=data.message.pk).update(is_trashed=True)
    assert_occurs(kind, data.party, False, None)


@pytest.mark.parametrize("mail", ["sent", "received", "none", "trashed", "chatter"])
@pytest.mark.parametrize("target", ["party", "circle"])
def test_correspondence_probe_finds_only_eligible_mail(correspondence, target, mail):
    """A party or circle occurs with mail it sent or received, and not with none, only trashed or only chatter.

    Trashed and chatter mail reaches the party through both arms, sender and recipient.
    """
    data = correspondence
    message = type(data.message)._base_manager.filter(pk=data.message.pk)
    if mail in ("received", "trashed", "chatter"):
        apps.get_model("messaging", "Participant")._base_manager.create(
            message=data.message, thread=data.thread, handle=data.handle, role="to",
        )
    if mail in ("received", "none"):
        message.update(sender=None)
    elif mail == "trashed":
        message.update(is_trashed=True)
    elif mail == "chatter":
        content_type, object_id = generic_pointer_target(data.party)
        apps.get_model("messaging", "ThreadAttachment")._base_manager.create(
            thread=data.thread, content_type=content_type, object_id=object_id, role="chatter",
        )
    record = data.party if target == "party" else data.circle
    assert_occurs(CorrespondenceKind(), record, mail in ("sent", "received"), None)


def test_correspondence_probe_applies_the_eligibility_inside_each_arm(correspondence):
    """The probe tests the existence of the membership union, each arm already eligible.

    With the eligibility outside a ``pk IN`` over every message, PostgreSQL answers
    ``LIMIT 1`` by scanning messages in order against the whole membership.
    """
    kind = CorrespondenceKind()
    kind.occurs(correspondence.circle)  # Warm native content-type identities.
    with CaptureQueriesContext(connection) as queries:
        assert kind.occurs(correspondence.circle)
    (sql,) = [query["sql"] for query in queries]
    trashed = f'{connection.ops.quote_name(type(correspondence.message)._meta.db_table)}."is_trashed"'
    members = _outer_union_members(sql)
    assert len(members) == 2
    assert all(trashed in member and "'chatter'" in member for member in members)


def _outer_union_members(sql: str) -> list[str]:
    """Split a statement at its outermost ``UNION ALL``; a union nested in a subquery stays in its member."""
    members, depth, start = [], 0, 0
    for match in re.finditer(r"[()]|UNION ALL", sql):
        if match.group() in "()":
            depth += 1 if match.group() == "(" else -1
        elif depth == 0:
            members.append(sql[start:match.start()])
            start = match.end()
    return [*members, sql[start:]]
