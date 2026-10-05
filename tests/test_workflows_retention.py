"""Retention composes run-tree ownership and native protected evidence lifecycles."""

from datetime import timedelta

import pytest
from django.apps import apps
from django.db import models
from django.db.models.functions import Now

from angee.base.scoping import system_queryset
from angee.decisions.testing.models import Decision
from angee.workflows.managers import PRUNE_BATCH_LIMIT
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRecord, StepRun, WorkflowRun
from tests.conftest import vault_for
from tests.tables import model_tables
from tests.test_workflows_children import age_runs
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review import answer, questions, start_review
from tests.test_workflows_review import review as review
from tests.workflow_steps import Value, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def protected_execution(execution):
    """Register one temporary domain row with native protected evidence links."""

    class RetentionEvidence(models.Model):
        """Represent a domain record retaining an execution or an answered seat."""

        attempt = models.ForeignKey(StepAttempt, on_delete=models.PROTECT, null=True, related_name="+")
        decision = models.ForeignKey(Decision, on_delete=models.PROTECT, null=True, related_name="+")

        class Meta:
            """Keep the probe in an installed registry for Django's deletion collector."""

            app_label = "workflows"
            db_table = "test_workflows_retention_evidence"

    try:
        with model_tables((RetentionEvidence,)):
            yield RetentionEvidence
    finally:
        del apps.all_models["workflows"][RetentionEvidence._meta.model_name]
        apps.clear_cache()


def retry_prune(run):
    """Advance only the marked retry deadline, retaining the real lifecycle state."""
    system_queryset(WorkflowRun).filter(pk=run.pk).update(prune_after=Now() - timedelta(seconds=1))


def test_prune_removes_derived_from_rows_with_the_run(execution):
    """Admission references follow run retention without protecting source deletion."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="evidence-retention", actor=actor)
    source = vault_for(actor, name="Retained input")
    run = start_run(workflow, actor=actor, subject=source)
    run_until(run)
    assert system_queryset(StepRecord).filter(run=run).count() == 1
    age_runs(run)
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(StepRecord).filter(run_id=run.pk).exists()


def test_prune_deletes_owned_descendants_including_recent_grandchildren(child_graph, register_step):
    """One expired root owns the lifetime of its complete three-level tree."""
    actor, _, admitted, build = child_graph
    grand_workflow = load_workflow(document("entry"), key="retained-grandchild", actor=actor)
    grandchildren = []

    class StartGrandchild(Step[Value, Value, None]):
        """End without awaiting, exercising the owned-child cancellation policy."""

        key = "retention_start_grandchild"

        def run(self, ctx):
            grandchildren.append(ctx.start_run(grand_workflow, input=ctx.input.model_dump()))
            return ctx.done(ctx.input)

    register_step(StartGrandchild)
    parent, _ = build(child_step=StartGrandchild.key)
    run_until(parent)
    child = admitted[0]
    run_until(child)
    grandchild = system_queryset(WorkflowRun).get(pk=grandchildren[0].pk)
    assert grandchild.status == "canceled" and child.status == "succeeded"
    runner.wake_runs(child.pk)
    run_until(parent)
    assert parent.status == "succeeded"
    age_runs(parent)
    run_ids = [parent.pk, child.pk, grandchild.pk]
    assert system_queryset(StepAttempt).filter(step_run__run_id__in=run_ids).exists()
    assert WorkflowRun.objects.prune() == 3
    assert not system_queryset(WorkflowRun).filter(pk__in=run_ids).exists()
    assert not system_queryset(StepRun).filter(run_id__in=run_ids).exists()
    assert not system_queryset(StepAttempt).filter(step_run__run_id__in=run_ids).exists()
    assert system_queryset(grand_workflow.versions.model).filter(workflow=grand_workflow).exists()


def test_prune_preserves_parent_cause_until_terminal_continuation_is_pruned(child_graph):
    """Even completed continuations retain the parent step that caused them."""
    _, _, admitted, build = child_graph
    parent, _ = build(relation="continuation", await_child=False)
    run_until(parent)
    child = admitted[0]
    run_until(child)
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 0
    parent.refresh_from_db()
    assert parent.prune_reason
    child.refresh_from_db()
    assert child.status == "succeeded" and child.output == {"value": 7}
    assert child.parent_step_id is not None
    assert child.relation == "continuation"
    assert system_queryset(StepAttempt).filter(step_run__run=child).exists()
    age_runs(child)
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(WorkflowRun).filter(pk=child.pk).exists()
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(WorkflowRun).filter(pk=parent.pk).exists()


def test_active_continuation_marks_parent_until_the_continuation_is_pruned(child_graph):
    """The explicit retention rule defers deletion while a continuation is active."""
    _, _, admitted, build = child_graph
    parent, _ = build(relation="continuation", await_child=False)
    run_until(parent)
    child = admitted[0]
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 0
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.prune_after is not None and parent.prune_reason == "A descendant is still running."
    assert child.status == "running" and child.parent_step_id is not None
    assert not system_queryset(WorkflowRun).retention_candidates().filter(pk=parent.pk).exists()
    run_until(child)
    assert WorkflowRun.objects.prune() == 0
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 0
    child.refresh_from_db()
    assert child.parent_step_id is not None and child.status == "succeeded"
    age_runs(child)
    assert WorkflowRun.objects.prune() == 1
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 1


def test_reprocess_cause_is_retained_until_its_replacement_is_pruned(execution):
    """A later run protects the exact predecessor it reprocessed."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), actor=actor)
    original = start_run(workflow, actor=actor)
    run_until(original)
    replacement = WorkflowRun.objects.reprocess(original, actor=actor)
    run_until(replacement)
    age_runs(original)
    assert WorkflowRun.objects.prune() == 0
    original.refresh_from_db()
    assert original.prune_reason
    assert replacement.reprocess_of_id == original.pk
    age_runs(replacement)
    assert WorkflowRun.objects.prune() == 1
    retry_prune(original)
    assert WorkflowRun.objects.prune() == 1


def test_protected_attempt_rolls_back_the_whole_owned_tree_and_wait_links(child_graph, protected_execution):
    """A domain reference blocks deletion without losing a previously deleted child."""
    _, _, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    child = admitted[0]
    run_until(child)
    runner.wake_runs(child.pk)
    run_until(parent)
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert waiter.awaited_run_id == child.pk
    attempt = system_queryset(StepAttempt).get(step_run__run=parent, step_run__node_key="start")
    evidence = protected_execution.objects.create(attempt=attempt)
    age_runs(parent)
    assert WorkflowRun.objects.prune() == 0
    parent.refresh_from_db()
    waiter.refresh_from_db()
    assert parent.prune_after is not None and parent.prune_reason
    assert waiter.awaited_run_id == child.pk
    assert system_queryset(WorkflowRun).filter(pk=child.pk).exists()
    assert system_queryset(StepAttempt).filter(pk=attempt.pk).exists()
    evidence.delete()
    retry_prune(parent)
    assert WorkflowRun.objects.prune() == 2




def test_prune_checks_only_twenty_five_roots_and_marked_blockers_do_not_starve_later_batches(child_graph):
    """The bound includes blocked candidates; their marks admit later roots next tick."""
    actor, _, admitted, build = child_graph
    blocked, _ = build(relation="continuation", await_child=False)
    run_until(blocked)
    workflow = load_workflow(document("entry"), key="retention-batch", actor=actor)
    roots = []
    for _ in range(26):
        run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 3})
        run_until(run)
        roots.append(run)
    age_runs(blocked, *roots)
    assert PRUNE_BATCH_LIMIT == 25
    assert WorkflowRun.objects.prune() == 24
    blocked.refresh_from_db()
    assert blocked.prune_after is not None
    assert system_queryset(WorkflowRun).filter(pk__in=[run.pk for run in roots]).count() == 2
    assert WorkflowRun.objects.prune() == 2
    assert WorkflowRun.objects.prune() == 0
    assert system_queryset(WorkflowRun).filter(pk__in=[blocked.pk, admitted[0].pk]).count() == 2


def test_retention_setting_keeps_recent_terminal_runs(execution, settings):
    """The configured age threshold applies before destructive cleanup begins."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 120
    workflow = load_workflow(document("entry"), actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 3})
    run_until(run)
    age_runs(run)
    assert WorkflowRun.objects.prune() == 0
    settings.ANGEE_WORKFLOW_RETENTION_DAYS = 90
    assert WorkflowRun.objects.prune() == 1

def test_run_pruning_retains_decisions_and_releases_the_asking_link(review):
    _, people, _, _ = review
    run, step = start_review(review)
    decision = questions(step)[0]
    answer(decision, people[0])
    run_until(run)
    age_runs(run)
    assert WorkflowRun.objects.prune() == 1
    retained = system_queryset(Decision).get(pk=decision.pk)
    assert not retained.requesting_steps.with_actor(people[0]).exists() and retained.verdict == ["approve"]
