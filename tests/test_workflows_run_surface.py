"""Resource-owned workflow reads, execution facets and public reference filters."""

import pytest
from django.db import IntegrityError, transaction

from angee.base.scoping import system_queryset
from angee.decisions.contracts import DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.workflows import schema as workflow_schema
from angee.workflows.reviews import ReviewStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow, WorkflowRun
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for
from tests.workflow_steps import document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


@pytest.fixture
def schema():
    """Use the addon's resource declarations through the shared schema builder."""
    return addon_schema(workflow_schema.schemas, "console")


def test_workflow_and_version_reads_follow_workflow_permission(schema, execution):
    """A starter reads publications, while unrelated identities remain hidden."""
    admin, _sent = execution
    starter, outsider = (create_user(name) for name in ("publication-starter", "publication-outsider"))
    workflow = load_workflow(
        document("entry"), key="visible-publication", name="Visible publication",
        subject_model="knowledge.vault", actor=admin,
    )
    original = workflow.published
    saved = Workflow.objects.save_draft(
        workflow, draft=document("entry", "finish"), expected_revision=workflow.draft_revision, actor=admin,
    )
    assert saved.status == "saved"
    current = Workflow.objects.publish(workflow, actor=admin).version
    hidden = load_workflow(document("entry"), key="hidden-publication", actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", starter)
    query = """query($id: String!, $key: String!, $model: String!) {
      workflow(where: {key: {_eq: $key}, subject_model: {_eq: $model}}, order_by: {name: asc}) {
        id key name description subject_model published { id number created_at }
      }
      workflow_by_pk(id: $id) { id published { id number } }
      workflow_aggregate { aggregate { count } }
      workflowversion(where: {workflow: {_eq: $id}}, order_by: {number: desc}) {
        id number content_hash created_at published_by { id } workflow { id }
      }
      workflowversion_aggregate(where: {workflow: {_eq: $id}}) { aggregate { count } }
    }"""
    variables = {"id": workflow.sqid, "key": workflow.key, "model": "knowledge.vault"}
    visible = result_data(execute_schema(schema, query, variables, user=starter))
    assert visible["workflow"] == [{
        "id": workflow.sqid, "key": workflow.key, "name": workflow.name, "description": "",
        "subject_model": "knowledge.Vault",
        "published": {"id": current.sqid, "number": 2, "created_at": current.created_at.isoformat()},
    }]
    assert visible["workflow_by_pk"] == {"id": workflow.sqid, "published": {"id": current.sqid, "number": 2}}
    assert visible["workflow_aggregate"] == {"aggregate": {"count": 1}}
    assert visible["workflowversion_aggregate"] == {"aggregate": {"count": 2}}
    versions = visible["workflowversion"]
    assert [(row["id"], row["number"]) for row in versions] == [(current.sqid, 2), (original.sqid, 1)]
    assert [row["content_hash"] for row in versions] == [current.content_hash, original.content_hash]
    assert all(row["created_at"] and row["workflow"] == {"id": workflow.sqid} for row in versions)
    # An author relation does not override the user resource's own read policy.
    assert all(row["published_by"] is None for row in versions)
    author_view = result_data(execute_schema(schema, query, variables, user=admin))
    assert all(row["published_by"] == {"id": admin.sqid} for row in author_view["workflowversion"])
    assert result_data(execute_schema(schema, query, variables, user=outsider)) == {
        "workflow": [], "workflow_by_pk": None, "workflow_aggregate": {"aggregate": {"count": 0}},
        "workflowversion": [], "workflowversion_aggregate": {"aggregate": {"count": 0}},
    }
    assert result_data(execute_schema(schema, query, {**variables, "id": hidden.sqid}, user=starter))[
        "workflow_by_pk"
    ] is None
    assert "draft" not in schema._schema.get_type("WorkflowType").fields
    assert "layout" not in schema._schema.get_type("WorkflowType").fields
    resources = {item.model_label: item for item in schema.angee_resources}
    assert {"key", "subject_model"} <= resources["workflows.Workflow"].query.fields.keys()
    assert resources["workflows.WorkflowVersion"].query.fields["workflow"].filter is not None


def test_run_workflow_facet_and_filter_count_only_the_viewers_execution(schema, execution):
    """A native workflow relation bucket drills into the starter's own rows."""
    admin, _sent = execution
    starter, other = (create_user(name) for name in ("facet-starter", "facet-other-starter"))
    first = load_workflow(document("entry"), key="first-facet", name="First facet", actor=admin)
    second = load_workflow(document("entry"), key="second-facet", name="Second facet", actor=admin)
    for workflow in (first, second):
        for actor in (starter, other):
            workflow.with_actor(admin).grant_record_access("starter", actor)
    own = [start_run(first, actor=starter), start_run(first, actor=starter), start_run(second, actor=starter)]
    foreign = start_run(first, actor=other)
    query = """query($id: String!) {
      workflowrun(where: {version__workflow: {_eq: $id}}) { id }
      workflowrun_aggregate { aggregate { count } }
      workflowrun_groups(group_by: [{field: VERSION__WORKFLOW}, {field: VERSION__WORKFLOW__NAME}]) {
        key { version__workflow_id version__workflow__name }
        aggregate { count }
      }
    }"""
    visible = result_data(execute_schema(schema, query, {"id": first.sqid}, user=starter))
    assert {row["id"] for row in visible["workflowrun"]} == {run.sqid for run in own[:2]}
    assert visible["workflowrun_aggregate"] == {"aggregate": {"count": 3}}
    assert {
        (row["key"]["version__workflow_id"], row["key"]["version__workflow__name"]): row["aggregate"]["count"]
        for row in visible["workflowrun_groups"]
    } == {(first.sqid, first.name): 2, (second.sqid, second.name): 1}
    other_view = result_data(execute_schema(schema, query, {"id": first.sqid}, user=other))
    assert other_view["workflowrun"] == [{"id": foreign.sqid}]
    assert other_view["workflowrun_aggregate"] == {"aggregate": {"count": 1}}
    resource = next(item for item in schema.angee_resources if item.model_label == "workflows.WorkflowRun")
    axis = resource.query.axes["version.workflow"]
    assert axis.kind == "relation"
    assert axis.server.key == "version__workflow_id"
    assert axis.server.label_key == "version__workflow__name"
    assert axis.drill.field == "version.workflow"
    assert resource.query.fields["version.workflow"].filter is not None


def test_run_subject_filters_use_model_labels_and_public_ids(schema, execution):
    """Exact subject filters compose in list and aggregate reads without widening access."""
    admin, _sent = execution
    starter, other = (create_user(name) for name in ("subject-filter-starter", "subject-filter-other"))
    workflow = load_workflow(document("entry"), actor=admin)
    for actor in (starter, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    subjects = [vault_for(starter, name=name) for name in ("First reference", "Second reference")]
    selected = start_run(workflow, actor=starter, subject=subjects[0])
    start_run(workflow, actor=starter, subject=subjects[1])
    start_run(workflow, actor=starter, subject=workflow)
    start_run(workflow, actor=other, subject=workflow)
    query = """query($where: workflowrun_bool_exp) {
      workflowrun(where: $where) { id subject_model subject_id }
      workflowrun_aggregate(where: $where) { aggregate { count } }
    }"""
    where = {"subject_model": {"_eq": "knowledge.Vault"}, "subject_id": {"_eq": subjects[0].sqid}}
    visible = result_data(execute_schema(schema, query, {"where": where}, user=starter))
    assert visible == {
        "workflowrun": [{"id": selected.sqid, "subject_model": "knowledge.Vault", "subject_id": subjects[0].sqid}],
        "workflowrun_aggregate": {"aggregate": {"count": 1}},
    }
    for actor, filters in (
        (other, where),
        (starter, {**where, "subject_model": {"_eq": "workflows.Workflow"}}),
        (starter, {"subject_id": {"_eq": subjects[0].sqid}, "subject_model": {"_eq": "unknown.Model"}}),
    ):
        assert result_data(execute_schema(schema, query, {"where": filters}, user=actor)) == {
            "workflowrun": [], "workflowrun_aggregate": {"aggregate": {"count": 0}},
        }
    only_model = result_data(execute_schema(
        schema, query, {"where": {"subject_model": {"_eq": "knowledge.vault"}}}, user=starter,
    ))
    assert only_model["workflowrun_aggregate"] == {"aggregate": {"count": 2}}
    assert result_data(execute_schema(
        schema, query, {"where": {"subject_id": {"_eq": subjects[0].sqid}}}, user=starter,
    )) == visible


def test_run_evidence_redacts_references_after_source_read_is_revoked(schema, execution):
    """Run readers keep the retained edge but cannot recover a hidden target ID."""
    admin, _sent = execution
    starter, viewer = (create_user(name) for name in ("evidence-starter", "evidence-viewer"))
    workflow = load_workflow(document("entry"), actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", starter)
    source = vault_for(starter, name="Retained source")
    run = start_run(workflow, actor=starter, subject=source)
    run.with_actor(starter).grant_record_access("reader", viewer)
    query = """query($id: String!) {
      workflowrun_by_pk(id: $id) { id subject_model subject_id evidence { id record_model record_id } }
    }"""
    own = result_data(execute_schema(schema, query, {"id": run.sqid}, user=starter))["workflowrun_by_pk"]
    assert len(own["evidence"]) == 1
    assert own["evidence"][0]["record_model"] == "knowledge.Vault"
    assert own["evidence"][0]["record_id"] == source.sqid
    assert own["subject_id"] == source.sqid
    hidden = result_data(execute_schema(schema, query, {"id": run.sqid}, user=viewer))["workflowrun_by_pk"]
    assert hidden["evidence"] == [{"id": own["evidence"][0]["id"], "record_model": None, "record_id": None}]
    assert hidden["subject_model"] is hidden["subject_id"] is None
    assert result_data(execute_schema(schema, """query($id: String!) {
      workflowrun(where: {subject_id: {_eq: $id}}) { id }
      workflowrun_aggregate(where: {subject_id: {_eq: $id}}) { aggregate { count } }
    }""", {"id": source.sqid}, user=viewer)) == {
        "workflowrun": [], "workflowrun_aggregate": {"aggregate": {"count": 0}},
    }


def test_for_subject_preserves_actor_and_existing_filters(execution):
    """Shared subjects do not turn a starter's query into another starter's runs."""
    admin, _sent = execution
    starter, other = (create_user(name) for name in ("subject-reader", "subject-other-reader"))
    workflow = load_workflow(document("entry"), actor=admin)
    for actor in (starter, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    own = start_run(workflow, actor=starter, subject=workflow)
    foreign = start_run(workflow, actor=other, subject=workflow)
    start_run(workflow, actor=starter)
    rows = WorkflowRun.objects.with_actor(starter).for_subject(workflow)
    assert list(rows.values_list("pk", flat=True)) == [own.pk]
    assert not rows.filter(pk=foreign.pk).exists()
    other_rows = WorkflowRun.objects.with_actor(other).for_subject(workflow)
    assert list(other_rows.values_list("pk", flat=True)) == [foreign.pk]


def test_run_origin_groups_and_filters_follow_admission_lineage_and_read_scope(schema, execution):
    """The stored admission fact serves scoped rows, groups and drill-down."""
    admin, _sent = execution
    starter, other = (create_user(name) for name in ("origin-starter", "origin-other"))
    workflow = load_workflow(document("entry"), actor=admin)
    replacements = []
    for actor in (starter, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
        original = start_run(workflow, actor=actor)
        run_until(original)
        replacements.append(WorkflowRun.objects.reprocess(original, actor=actor))
    query = """query($origin: String!) {
      workflowrun(where: {origin: {_eq: $origin}}) { id origin }
      workflowrun_aggregate(where: {origin: {_eq: $origin}}) { aggregate { count } }
      workflowrun_groups(group_by: [{field: ORIGIN}]) { key { origin } aggregate { count } }
    }"""
    for actor, replacement in zip((starter, other), replacements, strict=True):
        visible = result_data(execute_schema(schema, query, {"origin": "reprocess"}, user=actor))
        assert visible["workflowrun"] == [{"id": replacement.sqid, "origin": "REPROCESS"}]
        assert visible["workflowrun_aggregate"] == {"aggregate": {"count": 1}}
        assert {row["key"]["origin"]: row["aggregate"]["count"] for row in visible["workflowrun_groups"]} == {
            "MANUAL": 1, "REPROCESS": 1,
        }
    resource = next(item for item in schema.angee_resources if item.model_label == "workflows.WorkflowRun")
    axis = resource.query.axes["origin"]
    assert axis.server.input == "ORIGIN" and axis.server.key == "origin"
    assert axis.drill.field == "origin"
    assert resource.query.fields["origin"].filter is not None
    assert {item.from_value: item.to_value for item in axis.drill.value_map}["REPROCESS"] == "reprocess"

    # A native bulk update cannot change origin without violating the cause invariant.
    replacement = replacements[0]
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            system_queryset(WorkflowRun).filter(pk=replacement.pk).update(reprocess_of=None)
    replacement.refresh_from_db()
    assert replacement.origin == "reprocess"
    assert result_data(execute_schema(schema, query, {"origin": "reprocess"}, user=starter))["workflowrun"] == [
        {"id": replacement.sqid, "origin": "REPROCESS"},
    ]


class Accept(Action, key="accept", label="Accept", verdict=Verdict.COMPLETED, outcome="accepted"):
    """One response keeps the decision-group query proof focused on links."""


def test_step_decision_group_exact_filter_combines_with_status(schema, execution, register_step):
    """A review waiter exposes its admitted group through the normal step resource."""
    admin, _sent = execution
    starter, reviewer, other = (create_user(name) for name in ("group-starter", "group-reviewer", "group-other"))

    class Question(ReviewStep[None, None, None, None]):
        key = "run_surface_question"
        actions = (Accept,)

        def ask(self, ctx):
            return ctx.ask(DecisionRequest(kind="review", subject=None, assignees=(reviewer,), actions=self.actions))

        def apply(self, ctx, settled):
            return ctx.done(outcome=settled[0].action.outcome)

    register_step(Question)
    workflow = load_workflow({
        "nodes": {"entry": {"step": Question.key}},
        "results": [{"from": "entry", "when": ["accepted"], "as": "accepted"}],
    }, actor=admin)
    for actor in (starter, other):
        workflow.with_actor(admin).grant_record_access("starter", actor)
    runs = [start_run(workflow, actor=starter) for _ in range(2)]
    for run in runs:
        run_until(run)
    step = system_queryset(StepRun).select_related("decision_group").get(run=runs[0])
    group = step.decision_group
    query = """query($where: steprun_bool_exp) {
      steprun(where: $where) { id decision_group { id } }
      steprun_aggregate(where: $where) { aggregate { count } }
    }"""
    where = {"decision_group": {"_eq": group.sqid}, "status": {"_eq": "waiting"}}
    assert result_data(execute_schema(schema, query, {"where": where}, user=starter)) == {
        "steprun": [{"id": step.sqid, "decision_group": {"id": group.sqid}}],
        "steprun_aggregate": {"aggregate": {"count": 1}},
    }
    for actor, filters in ((other, where), (starter, {**where, "status": {"_eq": "ready"}})):
        assert result_data(execute_schema(schema, query, {"where": filters}, user=actor)) == {
            "steprun": [], "steprun_aggregate": {"aggregate": {"count": 0}},
        }
    resource = next(item for item in schema.angee_resources if item.model_label == "workflows.StepRun")
    assert resource.query.fields["decision_group"].filter is not None


def test_step_rank_orders_fork_join_nested_and_paginated_resources(schema, execution):
    """A retained position orders the whole execution and its resource pages alike."""
    actor, _sent = execution
    workflow = load_workflow({
        "nodes": {
            "finish": {"step": "echo", "join": "all", "input": {"from": "branch_a"}},
            "branch_b": {"step": "echo", "next": {"done": "finish"}},
            "start": {"step": "echo", "next": {"done": ["branch_b", "branch_a"]}},
            "branch_a": {"step": "echo", "next": {"done": "finish"}},
        },
        "results": [{"from": "finish"}],
    }, actor=actor)
    run = start_run(workflow, actor=actor)
    run_until(run)
    assert run.status == "succeeded"
    expected = [
        {"node_key": key, "rank": rank, "map_index": 0, "is_mapped": False, "is_map": False}
        for rank, key in enumerate(("start", "branch_a", "branch_b", "finish"))
    ]
    assert list(system_queryset(StepRun).filter(run=run).order_by("rank").values_list("node_key", "rank")) == [
        (row["node_key"], row["rank"]) for row in expected
    ]
    query = """query($id: String!, $offset: Int!) {
      steprun(where: {run: {_eq: $id}}, order_by: [{rank: asc}, {map_index: asc}], limit: 2, offset: $offset) {
        node_key rank map_index is_mapped is_map
      }
      workflowrun_by_pk(id: $id) { step_runs { node_key rank map_index is_mapped is_map } }
      narrow: workflowrun_by_pk(id: $id) { step_runs { node_key } }
    }"""
    for offset in (0, 2):
        result = result_data(execute_schema(schema, query, {"id": run.sqid, "offset": offset}, user=actor))
        assert result["steprun"] == expected[offset:offset + 2]
        assert result["workflowrun_by_pk"]["step_runs"] == expected
        assert result["narrow"]["step_runs"] == [{"node_key": row["node_key"]} for row in expected]
    resource = next(item for item in schema.angee_resources if item.model_label == "workflows.StepRun")
    assert resource.query.fields["rank"].sort is not None
    assert "rank" in schema._schema.get_type("steprun_order_by").fields


@pytest.mark.parametrize("node_key,map_index,expected", [
    ("ordinary", 0, False), ("body", 0, False), ("items_body", 0, False),
    ("items.body", 0, True), ("items.body", 3, True),
])
def test_map_body_fact_uses_node_identity_including_first_item(node_key, map_index, expected):
    """Unsaved values prove the model fact without inventing persisted map work."""
    row = StepRun(node_key=node_key, map_index=map_index)
    assert row.is_mapped is expected
