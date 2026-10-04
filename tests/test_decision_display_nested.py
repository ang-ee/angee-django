"""Execution display projections remain scoped and batched through nested decisions."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
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

        def continue_with(self, ctx, decision, outcome):
            return ctx.done(outcome=outcome)

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
        return system_queryset(Decision).get(requesting_steps=step)

    return workflow, owner, operator, assignee, admit




@pytest.mark.parametrize("viewer_index", [1, 2, 3])
def test_nested_question_uses_the_step_read_scope(schema, nested_reviews, viewer_index):
    _, _, _, _, admit = nested_reviews
    decision = admit()
    result = result_data(execute_schema(schema, "{ steprun { decision { id } } }", user=nested_reviews[viewer_index]))
    assert result == {"steprun": [] if viewer_index == 3 else [{"decision": {"id": decision.sqid}}]}

def test_nested_question_read_is_batched(schema, nested_reviews):
    _, owner, _, _, admit = nested_reviews
    query = "{ steprun { decision { id is_open } } }"
    def measure():
        result_data(execute_schema(schema, query, user=owner))
        with CaptureQueriesContext(connection) as captured:
            result = result_data(execute_schema(schema, query, user=owner))
        return len(captured), result["steprun"]
    admit()
    one_count, one = measure()
    for _ in range(4):
        admit()
    many_count, many = measure()
    assert len(one) == 1 and len(many) == 5
    assert many_count == one_count
