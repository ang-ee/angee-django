"""Composed decision links inherit execution visibility and native query axes."""

import pytest
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.graphql.schema import GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.reviews import ReviewStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import Decision, StepRun
from tests.conftest import SchemaAddon, create_user, execute_schema, result_data


class Accept(Action, value="accept", label="Accept", verdict=Verdict.COMPLETED, outcome="accepted"):
    """One plain answer keeps these proofs focused on contribution ownership."""


@pytest.fixture
def linked_decision(execution, register_step, workflow_permissions):
    """Compose permission contributions and reach a real review's decision wait."""

    admin, _sent = execution
    owner, operator, stranger, assignee = (
        create_user(name) for name in ("run-owner", "run-operator", "other-starter", "seat-assignee")
    )

    class Question(ReviewStep[None, None, None, None]):
        """Ask through the worker boundary that admits and links decision groups."""

        key = "linked_review"
        actions = (Accept,)

        def ask(self, ctx):
            """Offer one seat to a person with no execution grants."""
            return ctx.ask(DecisionRequest(
                kind="review", subject=None, assignees=(assignee,), actions=self.actions,
            ))

        def apply(self, ctx, settled):
            """Complete through the same declared action outcome."""
            return ctx.done(outcome=settled[0].action.outcome)

    register_step(Question)
    workflow = load_workflow({
        "nodes": {"entry": {"step": Question.key}},
        "results": [{"from": "entry", "when": ["accepted"], "as": "accepted"}],
    }, actor=admin)
    for person in (owner, stranger):
        workflow.with_actor(admin).grant_record_access("starter", person)
    run = start_run(workflow, actor=owner)
    run.with_actor(owner).grant_record_access("operator", operator)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert (step.status, step.waiting_kind) == ("waiting", "decision")
    decision = system_queryset(Decision).get(group_id=step.decision_group_id)
    return workflow, run, step, decision, owner, operator, stranger, assignee


@pytest.fixture
def schema():
    """Use both addon buckets including the native output and input donors."""

    return GraphQLSchemas([
        SchemaAddon(decision_schema.schemas), SchemaAddon(workflow_schema.schemas),
    ]).build("console")


def test_run_operator_reads_linked_decision_but_foreign_starter_cannot(schema, linked_decision):
    """The contributor adds run read to decisions without adding any act grant."""

    workflow, run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = """{ decisions { id group { step_run { id run { id version { workflow { id } } } } } } }"""
    assert result_data(execute_schema(schema, query, user=owner)) == {
        "decisions": [{"id": decision.sqid, "group": {"step_run": {
            "id": step.sqid, "run": {"id": run.sqid, "version": {"workflow": {"id": workflow.sqid}}},
        }}}],
    }
    assert result_data(execute_schema(schema, query, user=operator)) == {
        "decisions": [{"id": decision.sqid, "group": {"step_run": {
            "id": step.sqid, "run": {"id": run.sqid, "version": None},
        }}}],
    }
    assert result_data(execute_schema(schema, query, user=stranger)) == {"decisions": []}
    # A seat's own read permission does not disclose an unrelated execution.
    assert result_data(execute_schema(schema, query, user=assignee)) == {
        "decisions": [{"id": decision.sqid, "group": {"step_run": None}}],
    }
    with actor_context(operator):
        assert not decision.with_actor(operator).has_access("act")


@pytest.mark.parametrize("path,index", [
    ("group__step_run", 2), ("group__step_run__run", 1), ("group__step_run__run__version__workflow", 0),
])
def test_decision_execution_paths_filter_by_public_identity(schema, linked_decision, path, index):
    """Native nested filters expose each persisted link to inbox and dashboards."""

    decision, owner, operator, stranger = linked_decision[3:7]
    target = linked_decision[index]
    query = f"""query($id: String!) {{
      decisions(where: {{{path}: {{_eq: $id}}}}) {{ id }}
      decisions_aggregate(where: {{_and: [{{{path}: {{_in: [$id]}}}}]}}) {{ aggregate {{ count }} }}
    }}"""
    assert result_data(execute_schema(schema, query, {"id": target.sqid}, user=owner)) == {
        "decisions": [{"id": decision.sqid}],
        "decisions_aggregate": {"aggregate": {"count": 1}},
    }
    assert result_data(execute_schema(schema, query, {"id": target.sqid}, user=operator)) == {
        "decisions": [] if index == 0 else [{"id": decision.sqid}],
        "decisions_aggregate": {"aggregate": {"count": 0 if index == 0 else 1}},
    }
    assert result_data(execute_schema(schema, query, {"id": target.sqid}, user=stranger)) == {
        "decisions": [], "decisions_aggregate": {"aggregate": {"count": 0}},
    }
    resource = next(resource for resource in schema.angee_resources if resource.model_label == "decisions.Decision")
    assert resource.query.fields[path.replace("__", ".")].filter is not None


def test_decision_display_fields_follow_related_read_permissions(schema, linked_decision):
    """Inbox columns do not disclose execution labels to a seat-only reader."""
    workflow, _run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = "{ decisions { id workflow_name node_key } }"
    for actor, name, key in ((owner, workflow.name, step.node_key), (operator, None, step.node_key),
                             (assignee, None, None)):
        assert result_data(execute_schema(schema, query, user=actor)) == {
            "decisions": [{"id": decision.sqid, "workflow_name": name, "node_key": key}],
        }
    assert result_data(execute_schema(schema, query, user=stranger)) == {"decisions": []}
    resource = next(item for item in schema.angee_resources if item.model_label == "decisions.Decision")
    for name in ("workflow_name", "node_key"):
        assert name in {field.name for field in resource.fields}
        assert resource.query.fields[name].filter is not None


@pytest.mark.parametrize("field,index,attribute", [("workflow_name", 0, "name"), ("node_key", 2, "node_key")])
def test_decision_display_filters_and_dashboard_counts_do_not_leak(schema, linked_decision, field, index, attribute):
    """Exact filter aliases share the guarded value across lists, counts and stored filters."""
    decision, owner, operator, stranger, assignee = linked_decision[3:]
    value = getattr(linked_decision[index], attribute)
    where = {field: {"_eq": value}}
    query = """query($where: decisions_bool_exp) {
      decisions(where: $where) { id }
      decisions_aggregate(where: $where) { aggregate { count } }
    }"""
    schemas = GraphQLSchemas([SchemaAddon(decision_schema.schemas), SchemaAddon(workflow_schema.schemas)])
    condition = schemas.resource_filter(Decision, where)
    for actor, visible in ((owner, True), (operator, field == "node_key"), (stranger, False), (assignee, False)):
        assert result_data(execute_schema(schema, query, {"where": where}, user=actor)) == {
            "decisions": [{"id": decision.sqid}] if visible else [],
            "decisions_aggregate": {"aggregate": {"count": int(visible)}},
        }
        assert list(condition(Decision.objects.with_actor(actor)).values_list("pk", flat=True)) == (
            [decision.pk] if visible else []
        )
