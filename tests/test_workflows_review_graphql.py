"""Composed decision links inherit execution visibility and native query axes."""

import asyncio

import pytest
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql import subscriptions
from angee.graphql.events import ChangePayload
from angee.graphql.schema import GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.reviews import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import SchemaAddon, create_user, execute_schema, result_data, vault_for


@pytest.fixture
def linked_decision(execution, register_step, composed_permissions):
    """Compose permission contributions and reach a real review's decision wait."""

    admin, _sent = execution
    owner, operator, stranger, assignee = (
        create_user(name) for name in ("run-owner", "run-operator", "other-starter", "seat-assignee")
    )

    reference = vault_for(owner)
    write_relationships([RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(assignee))])

    class Question(DecisionStep[None, None, None]):
        """Ask through the worker boundary that admits and links decision groups."""

        key = "linked_review"
        outcomes = {"accepted": "Accepted"}

        def ask(self, ctx):
            """Offer one seat to a person with no execution grants."""
            return ctx.ask(
                DecisionRequest(
                    kind="review",
                    records=(reference,),
                    assignees=(assignee,),
                    proposal=DecisionProposal(
                        alternatives=[{"key": "accept", "label": "Accept", "outcome": "accepted"}]
                    ),
                )
            )

        def continue_with(self, ctx, decisions, outcomes):
            """Complete through the same declared action outcome."""
            return ctx.done(outcome=next(iter(outcomes)))

    register_step(Question)
    workflow = load_workflow(
        {
            "nodes": {"entry": {"step": Question.key}},
            "results": [{"from": "entry", "when": ["accepted"], "as": "accepted"}],
        },
        actor=admin,
    )
    for person in (owner, stranger):
        workflow.with_actor(admin).grant_record_access("starter", person)
    run = start_run(workflow, actor=owner)
    run.with_actor(owner).grant_record_access("operator", operator)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert (step.status, step.waiting_kind) == ("waiting", "decision")
    decision = system_queryset(Decision).get(step_run=step)
    return workflow, run, step, decision, owner, operator, stranger, assignee


@pytest.fixture
def schema():
    """Use both addon buckets including the native output and input donors."""

    return GraphQLSchemas(
        [
            SchemaAddon(decision_schema.schemas),
            SchemaAddon(workflow_schema.schemas),
        ]
    ).build("console")


def test_run_operator_reads_linked_decision_but_foreign_starter_cannot(schema, linked_decision):
    """The contributor adds run read to decisions without adding any act grant."""

    workflow, run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = """{ decisions { id step_run { id run { id version { workflow { id } } } } } }"""
    assert result_data(execute_schema(schema, query, user=owner)) == {
        "decisions": [
            {
                "id": decision.sqid,
                "step_run": {
                    "id": step.sqid,
                    "run": {"id": run.sqid, "version": {"workflow": {"id": workflow.sqid}}},
                },
            }
        ],
    }
    assert result_data(execute_schema(schema, query, user=operator)) == {
        "decisions": [
            {
                "id": decision.sqid,
                "step_run": {
                    "id": step.sqid,
                    "run": {"id": run.sqid, "version": None},
                },
            }
        ],
    }
    assert result_data(execute_schema(schema, query, user=stranger)) == {"decisions": []}
    # A seat's own read permission does not disclose an unrelated execution.
    assert result_data(execute_schema(schema, query, user=assignee)) == {
        "decisions": [{"id": decision.sqid, "step_run": None}],
    }
    with actor_context(operator):
        assert not decision.with_actor(operator).has_access("act")


@pytest.mark.parametrize("role", ["reader", "viewer"])
def test_run_readers_do_not_inherit_review_evidence(schema, linked_decision, execution, role):
    """Execution read grants do not turn their recipients into review operators."""
    workflow, run, _step, decision, owner, operator, _stranger, _assignee = linked_decision
    viewer = create_user(f"review-{role}")
    target = run if role == "reader" else workflow
    target.with_actor(execution[0]).grant_record_access(role, viewer)
    assert run.with_actor(viewer).has_access("read")
    assert not decision.with_actor(viewer).has_access("read")
    assert result_data(execute_schema(schema, "{ decisions { id } }", user=viewer)) == {
        "decisions": [],
    }
    assert decision.with_actor(operator).has_access("read")
    assert decision.with_actor(owner).has_access("read")


@pytest.mark.parametrize(
    "path,index",
    [
        ("step_run", 2),
        ("step_run__run", 1),
        ("step_run__run__version__workflow", 0),
    ],
)
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
        "decisions": [],
        "decisions_aggregate": {"aggregate": {"count": 0}},
    }
    resource = next(resource for resource in schema.angee_resources if resource.model_label == "decisions.Decision")
    assert resource.query.fields[path.replace("__", ".")].filter is not None


def test_workflow_key_filters_preserve_related_workflow_read_scope(schema, linked_decision):
    """A readable run or seat cannot be found through a hidden workflow key."""
    workflow, run, _step, decision, owner, operator, _stranger, assignee = linked_decision
    query = """query($key: String!) {
      workflowrun(where: {version__workflow__key: {_eq: $key}}) { id }
      workflowrun_aggregate(where: {version__workflow__key: {_eq: $key}}) { aggregate { count } }
      decisions(where: {step_run__run__version__workflow__key: {_eq: $key}}) { id }
      decisions_aggregate(where: {step_run__run__version__workflow__key: {_eq: $key}}) {
        aggregate { count }
      }
      visible: decisions { id }
      visible_count: decisions_aggregate { aggregate { count } }
    }"""
    expected = {
        "workflowrun": [{"id": run.sqid}],
        "workflowrun_aggregate": {"aggregate": {"count": 1}},
        "decisions": [{"id": decision.sqid}],
        "decisions_aggregate": {"aggregate": {"count": 1}},
        "visible": [{"id": decision.sqid}],
        "visible_count": {"aggregate": {"count": 1}},
    }
    assert result_data(execute_schema(schema, query, {"key": workflow.key}, user=owner)) == expected
    for actor in (operator, assignee):
        assert run.with_actor(actor).has_access("read") is (actor == operator)
        assert decision.with_actor(actor).has_access("read")
        assert not workflow.with_actor(actor).has_access("read")
        assert result_data(execute_schema(schema, query, {"key": workflow.key}, user=actor)) == {
            "workflowrun": [],
            "workflowrun_aggregate": {"aggregate": {"count": 0}},
            "decisions": [],
            "decisions_aggregate": {"aggregate": {"count": 0}},
            "visible": [{"id": decision.sqid}],
            "visible_count": {"aggregate": {"count": 1}},
        }
    resources = {resource.model_label: resource for resource in schema.angee_resources}
    assert resources["workflows.WorkflowRun"].query.fields["version.workflow.key"].filter is not None
    assert resources["decisions.Decision"].query.fields["step_run.run.version.workflow.key"].filter is not None


def test_run_and_decision_change_roots_scope_each_subscriber(schema, linked_decision, monkeypatch):
    """Source-composed live roots deliver only rows readable by their subscriber."""
    _workflow, run, _step, decision, owner, operator, stranger, assignee = linked_decision
    expected = {"workflowRunChanged", "decisionChanged"}
    assert expected <= schema._schema.subscription_type.fields.keys()
    assert all(f"{field}: ChangeEvent!" in schema.as_str() for field in expected)
    resources = {resource.model_label: resource for resource in schema.angee_resources}

    for model, row, surface, field, readers, nonreaders in (
        (
            type(run),
            run,
            workflow_schema.schemas["console"]["subscription"][0],
            "workflowRunChanged",
            (owner, operator),
            (assignee, stranger),
        ),
        (
            type(decision),
            decision,
            decision_schema.schemas["console"]["subscription"][0],
            "decisionChanged",
            (owner, operator, assignee),
            (stranger,),
        ),
    ):
        assert resources[model._meta.label].roots.changes_name == field
        payload = ChangePayload.from_instance(row, action="update", update_fields=None).as_message()

        async def stream(subscribed_model):
            assert subscribed_model is model
            yield payload

        monkeypatch.setattr(subscriptions, "subscribe", stream)
        resolver = surface.__strawberry_definition__.fields[0].base_resolver.wrapped_func

        async def receive(actor):
            with actor_context(actor):
                return [event async for event in resolver(object(), object())]

        for actor in readers:
            assert [event.id for event in asyncio.run(receive(actor))] == [row.sqid]
        for actor in nonreaders:
            assert asyncio.run(receive(actor)) == []


def test_decision_display_fields_follow_related_read_permissions(schema, linked_decision):
    """Inbox columns do not disclose execution labels to a seat-only reader."""
    workflow, _run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = "{ decisions { id workflow_name node_key } }"
    for actor, name, key in (
        (owner, workflow.name, step.node_key),
        (operator, None, step.node_key),
        (assignee, None, None),
    ):
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


def test_stored_can_act_filter_uses_the_queryset_actor(schema, linked_decision):
    """Permission expressions follow an explicit queryset actor over ambient identity."""
    decision, owner, operator, stranger, assignee = linked_decision[3:]
    schemas = GraphQLSchemas([SchemaAddon(decision_schema.schemas), SchemaAddon(workflow_schema.schemas)])
    condition = schemas.resource_filter(Decision, {"can_act": {"_eq": True}})
    query = "{ decisions(where: {can_act: {_eq: true}}) { id } }"
    for actor, visible in ((owner, False), (operator, False), (stranger, False), (assignee, True)):
        assert result_data(execute_schema(schema, query, user=actor)) == {
            "decisions": [{"id": decision.sqid}] if visible else [],
        }
        with actor_context(stranger if visible else assignee):
            assert list(condition(Decision.objects.with_actor(actor)).values_list("pk", flat=True)) == (
                [decision.pk] if visible else []
            )
