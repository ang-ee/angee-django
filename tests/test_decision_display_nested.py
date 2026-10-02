"""Execution display projections remain scoped and batched through nested decisions."""

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.data.hasura import with_filter_aliases
from angee.workflows.reviews import ReviewStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import create_user, execute_schema, result_data, vault_for
from tests.test_workflows_review_graphql import Accept
from tests.test_workflows_review_graphql import schema as schema


@pytest.fixture
def nested_reviews(execution, register_step, composed_permissions):
    """Reach evidence and supersession only through real review admission."""
    admin, _sent = execution
    owner, operator, assignee = (create_user(name) for name in (
        "nested-owner", "nested-operator", "nested-assignee",
    ))
    reference = vault_for(owner)
    write_relationships([
        RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(assignee)),
    ])

    class Question(ReviewStep[None, None, None, None]):
        key = "nested_display_review"
        actions = (Accept,)

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(
                kind="nested_display_review", subject=reference, assignees=(assignee,), actions=self.actions,
                supersede=ctx.input.get("supersede", False),
                context=DecisionContext(references=(DecisionRecordReference(
                    model=reference._meta.label, id=reference.sqid,
                ),)),
            ))

        def apply(self, ctx, settled):
            return ctx.done(outcome=settled[0].action.outcome)

    register_step(Question)
    workflow = load_workflow({
        "nodes": {"review": {"step": Question.key}},
        "results": [{"from": "review", "when": ["accepted"], "as": "accepted"}],
    }, actor=admin, name="Nested review")
    workflow.with_actor(admin).grant_record_access("starter", owner)

    def admit(*, supersede=False):
        run = start_run(workflow, actor=owner, input={"supersede": supersede})
        run.with_actor(owner).grant_record_access("operator", operator)
        run_until(run)
        step = system_queryset(StepRun).get(run=run)
        assert step.status == "waiting"
        return system_queryset(Decision).get(group_id=step.decision_group_id)

    return workflow, owner, operator, assignee, admit


@pytest.mark.parametrize("viewer_index", [1, 2, 3])
def test_evidence_nested_decision_display_inherits_each_related_read_scope(schema, nested_reviews, viewer_index):
    workflow, _owner, _operator, _assignee, admit = nested_reviews
    decision = admit()
    actor = nested_reviews[viewer_index]
    result = result_data(execute_schema(schema, """{
      decision_evidence { decision { id workflow_name node_key } }
    }""", user=actor))
    assert result == {"decision_evidence": [{"decision": {
        "id": decision.sqid,
        "workflow_name": workflow.name if viewer_index == 1 else None,
        "node_key": "review" if viewer_index != 3 else None,
    }}]}


@pytest.mark.parametrize("viewer_index", [1, 2, 3])
def test_superseded_by_nested_decision_projects_the_replacement(schema, nested_reviews, viewer_index):
    workflow, _owner, _operator, _assignee, admit = nested_reviews
    previous = admit(supersede=True)
    replacement = admit(supersede=True)
    previous.refresh_from_db()
    assert previous.superseded_by_id == replacement.pk
    result = result_data(execute_schema(schema, """query($id: String!) {
      decisions(where: {id: {_eq: $id}}) { superseded_by { id workflow_name node_key } }
    }""", {"id": previous.sqid}, user=nested_reviews[viewer_index]))
    assert result == {"decisions": [{"superseded_by": {
        "id": replacement.sqid,
        "workflow_name": workflow.name if viewer_index == 1 else None,
        "node_key": "review" if viewer_index != 3 else None,
    }}]}


@pytest.mark.parametrize("viewer_index,ambient_index", [(1, 3), (3, 1)])
def test_alias_projection_uses_the_queryset_actor(nested_reviews, viewer_index, ambient_index):
    """A pinned row actor also owns related scalar visibility under another ambient actor."""

    workflow, _owner, _operator, _assignee, admit = nested_reviews
    decision = admit()
    with actor_context(nested_reviews[ambient_index]):
        rows = with_filter_aliases(Decision.objects.with_actor(nested_reviews[viewer_index]))
        rows = rows.annotate(workflow_name=models.F("workflow_name"), node_key=models.F("node_key"))
        projected = rows.values("workflow_name", "node_key").get(pk=decision.pk)
    assert projected == {
        "workflow_name": workflow.name if viewer_index == 1 else None,
        "node_key": "review" if viewer_index == 1 else None,
    }


def test_nested_decision_display_does_not_add_queries_per_row(schema, nested_reviews):
    """Narrow evidence selections use one native prefetch as the collection grows."""
    _workflow, owner, _operator, _assignee, admit = nested_reviews
    query = "{ decision_evidence { decision { workflow_name node_key } } }"

    def measure():
        # Warm metadata and permission caches before comparing native query work.
        result_data(execute_schema(schema, query, user=owner))
        with CaptureQueriesContext(connection) as captured:
            result = result_data(execute_schema(schema, query, user=owner))
        return len(captured), result["decision_evidence"]

    admit()
    single_count, single = measure()
    for _ in range(4):
        admit()
    many_count, many = measure()
    assert len(single) == 1 and len(many) == 5
    assert many_count == single_count
    assert all(row == {"decision": {"workflow_name": "Nested review", "node_key": "review"}} for row in many)
