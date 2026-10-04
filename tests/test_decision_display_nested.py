"""Execution display projections remain scoped and batched through nested decisions."""

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.data.hasura import with_filter_aliases
from angee.workflows.reviews import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import create_user, execute_schema, result_data, vault_for
from tests.test_workflows_review_graphql import schema as schema


@pytest.fixture
def nested_reviews(execution, register_step, composed_permissions):
    """Reach evidence and concern links only through real review admission."""
    admin, _sent = execution
    owner, operator, assignee = (
        create_user(name)
        for name in (
            "nested-owner",
            "nested-operator",
            "nested-assignee",
        )
    )
    reference = vault_for(owner)
    write_relationships(
        [
            RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(assignee)),
        ]
    )

    class Question(DecisionStep[None, None, None]):
        key = "nested_display_review"
        outcomes = {"accepted": "Accepted"}

        def ask(self, ctx):
            return ctx.ask(
                DecisionRequest(
                    kind="nested_display_review",
                    records=(reference,),
                    assignees=(assignee,),
                    proposal=DecisionProposal(
                        alternatives=[{"key": "accept", "label": "Accept", "outcome": "accepted"}]
                    ),
                    context=DecisionContext(
                        references=(
                            DecisionRecordReference(
                                model=reference._meta.label,
                                id=reference.sqid,
                            ),
                        )
                    ),
                )
            )

        def continue_with(self, ctx, decisions, outcomes):
            return ctx.done(outcome=next(iter(outcomes)))

    register_step(Question)
    workflow = load_workflow(
        {
            "nodes": {"review": {"step": Question.key}},
            "results": [{"from": "review", "when": ["accepted"], "as": "accepted"}],
        },
        actor=admin,
        name="Nested review",
    )
    workflow.with_actor(admin).grant_record_access("starter", owner)

    def admit():
        run = start_run(workflow, actor=owner)
        run.with_actor(owner).grant_record_access("operator", operator)
        run_until(run)
        step = system_queryset(StepRun).get(run=run)
        assert step.status == "waiting"
        return system_queryset(Decision).get(step_run=step)

    return workflow, owner, operator, assignee, admit


@pytest.mark.parametrize("viewer_index", [1, 2, 3])
def test_evidence_nested_decision_display_inherits_each_related_read_scope(schema, nested_reviews, viewer_index):
    workflow, _owner, _operator, _assignee, admit = nested_reviews
    decision = admit()
    actor = nested_reviews[viewer_index]
    result = result_data(
        execute_schema(
            schema,
            """{
      decision_records { decision { id workflow_name node_key } }
    }""",
            user=actor,
        )
    )
    assert result == {
        "decision_records": [
            {
                "decision": {
                    "id": decision.sqid,
                    "workflow_name": workflow.name if viewer_index == 1 else None,
                    "node_key": "review" if viewer_index != 3 else None,
                }
            }
        ]
    }


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
    query = "{ decision_records { decision { workflow_name node_key } } }"

    def measure():
        # Warm metadata and permission caches before comparing native query work.
        result_data(execute_schema(schema, query, user=owner))
        with CaptureQueriesContext(connection) as captured:
            result = result_data(execute_schema(schema, query, user=owner))
        return len(captured), result["decision_records"]

    admit()
    single_count, single = measure()
    for _ in range(4):
        admit()
    many_count, many = measure()
    assert len(single) == 1 and len(many) == 5
    assert many_count == single_count
    assert all(row == {"decision": {"workflow_name": "Nested review", "node_key": "review"}} for row in many)
