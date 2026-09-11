"""Tests for workflow definition models."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from django.utils.text import slugify
from pydantic import BaseModel
from pydantic import Field as PydanticField
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows.attempts import JsonPresence
from angee.workflows.models import (
    TriggerKind,
    WorkflowPurpose,
    WorkflowStatus,
)
from angee.workflows.steps import StepImpl, StepOutcome
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.workflows import (
    Edge,
    Step,
    Trigger,
    Workflow,
    WorkflowRun,
    start_run,
    step_run_for,
    workflow_with_steps,
)

User = get_user_model()


class _ContractProbeConfig(BaseModel):
    """Typed config fixture for workflow operation metadata."""

    mode: str = PydanticField(default="safe", description="Execution mode.")


class _ContractProbeInput(BaseModel):
    """Typed input fixture for workflow operation metadata."""

    payload: str


class _ContractProbeOutput(BaseModel):
    """Typed output fixture for workflow operation metadata."""

    accepted: bool


class ContractProbeStep(StepImpl):
    """Additive registered step used to prove the operation query contract."""

    key = "contract_probe"
    label = "Contract probe"
    category = "Tests"
    description = "Exercise typed operation metadata."
    defaults = {"config": {"mode": "safe"}}
    config_model = _ContractProbeConfig
    input_model = _ContractProbeInput
    output_model = _ContractProbeOutput
    outcomes = (StepOutcome("accepted", "Accepted", "The payload was accepted."),)
    subject_declaration = "tests.workflow"


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ({"outcomes": (StepOutcome("", "Blank"),)}, "keys and labels must be non-blank"),
        ({"outcomes": (StepOutcome("needs review", "Review"),)}, "invalid outcome key 'needs review'"),
        (
            {"outcomes": (StepOutcome("done", "Done"), StepOutcome("done", "Again"))},
            "duplicate outcome key 'done'",
        ),
        ({"subject_declaration": "WrongShape"}, "invalid subject label 'WrongShape'"),
        ({"subject_declaration": 7}, "invalid subject label 7"),
        ({"effect": "external"}, "invalid effect 'external'"),
    ],
)
def test_step_operation_rejects_malformed_declarations(
    declaration: dict[str, Any],
    message: str,
) -> None:
    """Operation metadata fails at its registered implementation key."""

    malformed = type("MalformedStep", (StepImpl,), declaration)

    with pytest.raises(ImproperlyConfigured, match=message):
        malformed.operation(key="malformed_probe")


def create_workflow(name: str = "Document Review") -> Workflow:
    """Create one draft workflow in the test table."""

    return Workflow.objects.create(key=slugify(name), name=name)


def create_entry(workflow: Workflow, *, key: str = "start", name: str = "Start") -> Step:
    """Create one entry step for ``workflow``."""

    return Step.objects.create(
        workflow=workflow,
        key=key,
        name=name,
        step_class="agent_session",
        is_entry=True,
    )


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _console_schema() -> Any:
    """Build the workflows console schema against the concrete test models."""

    importlib.import_module("tests.test_workflows_engine")
    workflows_schema = importlib.import_module("angee.workflows.schema")
    return GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")


def _published_workflow(
    *,
    name: str,
    subject_declaration: str,
    owner: Any,
    purpose: WorkflowPurpose = WorkflowPurpose.AUTOMATION,
) -> tuple[Workflow, Workflow]:
    """Seed one actor-owned workflow lineage and publish its first version."""

    with system_context(reason="test workflows subject declaration definition"):
        draft = Workflow.objects.create(
            key=slugify(name),
            name=name,
            purpose=purpose,
            subject_declaration=subject_declaration,
            created_by=owner,
            updated_by=owner,
        )
        create_entry(draft)
        return draft, draft.publish()


@pytest.mark.django_db(transaction=True)
def test_run_start_validates_only_new_exact_admission(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Exact retries bypass mutable checks while changed identities remain rejected."""

    del workflow_engine_tables, no_workflow_queue
    owner = User.objects.create_user(username="workflow-admission-owner")
    other = User.objects.create_user(username="workflow-admission-other")
    subject, published = _published_workflow(
        name="Admission identity",
        subject_declaration=Workflow._meta.label,
        owner=owner,
    )
    validations: list[str] = []

    def validate_new() -> None:
        validations.append("called")

    def reject_new() -> None:
        raise ValidationError("configuration changed")

    run = WorkflowRun.objects.start(
        published, subject, owner, dedup_key="admission:exact",
        input=JsonPresence(True, {"scope": "frozen"}), validate_new=validate_new,
    )
    retained = WorkflowRun.objects.start(
        published, subject, owner, dedup_key="admission:exact",
        input=JsonPresence(True, {"scope": "frozen"}),
        validate_new=lambda: pytest.fail("retained admission was revalidated"),
    )

    assert retained.pk == run.pk
    assert validations == ["called"]
    with pytest.raises(ValidationError, match="different immutable facts"):
        WorkflowRun.objects.start(
            published, subject, owner, dedup_key="admission:exact",
            input=JsonPresence(True, {"scope": "changed"}),
        )
    with pytest.raises(ValidationError, match="different immutable facts"):
        WorkflowRun.objects.start(
            published, subject, other, dedup_key="admission:exact",
            input=JsonPresence(True, {"scope": "frozen"}),
        )
    with pytest.raises(ValidationError, match="configuration changed"):
        WorkflowRun.objects.start(
            published, subject, owner, dedup_key="admission:rejected",
            input=JsonPresence(True, {"scope": "new"}),
            validate_new=reject_new,
        )
    with system_context(reason="test rejected workflow admission"):
        assert not WorkflowRun.objects.filter(dedup_key="admission:rejected").exists()


@pytest.mark.django_db(transaction=True)
def test_publish_requires_exactly_one_entry_step(workflow_tables: None) -> None:
    """Publishing validates that a definition has exactly one entry step."""

    with system_context(reason="test workflows publish validation"):
        workflow = create_workflow()

        with pytest.raises(ValidationError, match="exactly one entry"):
            workflow.publish()

        create_entry(workflow)
        Step.objects.create(workflow=workflow, key="other", name="Other", is_entry=True)

        with pytest.raises(ValidationError, match="exactly one entry"):
            workflow.publish()


@pytest.mark.django_db(transaction=True)
def test_step_and_edge_definition_validation(workflow_tables: None) -> None:
    """Step classes and graph edges validate at the model boundary."""

    with system_context(reason="test workflows definition validation"):
        first = create_workflow("First")
        second = create_workflow("Second")
        source = create_entry(first, key="source", name="Source")
        target = Step.objects.create(workflow=first, key="target", name="Target")
        other_target = create_entry(second, key="other", name="Other")

        with pytest.raises(ValidationError, match="step_class"):
            Step.objects.create(workflow=first, key="bad", name="Bad", step_class="missing")

        with pytest.raises(ValidationError, match="config"):
            Step.objects.create(workflow=first, key="bad-config", name="Bad config", config=["not", "an", "object"])

        Edge.objects.create(workflow=first, source=source, target=target, condition="ok")

        with pytest.raises(ValidationError, match="same workflow"):
            Edge.objects.create(workflow=first, source=source, target=other_target, condition="wrong")


@pytest.mark.django_db(transaction=True)
def test_publish_copies_draft_to_immutable_version(workflow_tables: None) -> None:
    """Publishing copies the draft graph and later draft edits do not alter versions."""

    with system_context(reason="test workflows publish copy"):
        draft = create_workflow()
        entry = create_entry(draft)
        finish = Step.objects.create(
            workflow=draft,
            key="finish",
            name="Finish",
            step_class="agent_session",
        )
        Edge.objects.create(workflow=draft, source=entry, target=finish, condition="done")

        first = draft.publish()
        draft.name = "Document Review Draft"
        draft.save()
        entry.name = "Start Draft"
        entry.save()
        second = draft.publish()

        first.refresh_from_db()
        assert first.status == WorkflowStatus.PUBLISHED
        assert first.published_from == draft
        assert first.key == draft.key == "document-review"
        assert first.version == 1
        assert first.name == "Document Review"
        assert Step.objects.get(workflow=first, key="start").name == "Start"
        assert Edge.objects.filter(workflow=first, condition="done").count() == 1

        assert second.version == 2
        assert second.key == draft.key
        assert second.name == "Document Review Draft"
        assert Step.objects.get(workflow=second, key="start").name == "Start Draft"
        assert Workflow.objects.current_published_for(draft) == second

        first.name = "Edited"
        with pytest.raises(ValidationError, match="immutable"):
            first.save()

        published_step = Step.objects.get(workflow=first, key="start")
        published_step.name = "Edited"
        with pytest.raises(ValidationError, match="immutable"):
            published_step.save()

        draft.key = "changed-lineage"
        with pytest.raises(ValidationError, match="immutable once assigned"):
            draft.save()


@pytest.mark.django_db(transaction=True)
def test_lineage_projection_is_current_for_heads_versions_and_retirement(
    workflow_tables: None,
    django_assert_num_queries: Any,
) -> None:
    """One query projects stable lineage identity and latest publication state."""

    with system_context(reason="test workflow lineage projection"):
        head = create_workflow("Projected lineage")
        create_entry(head)
        first = head.publish()
        head.subject_declaration = Workflow._meta.label_lower
        head.save()
        second = head.publish()

        with django_assert_num_queries(1):
            rows = list(
                Workflow.objects.filter(pk__in=(head.pk, first.pk, second.pk)).with_lineage_projection().order_by("pk")
            )
            assert {row._workflow_lineage_id for row in rows} == {head.pk}
            assert {row._workflow_current_published_pk for row in rows} == {second.pk}
            assert {row._workflow_current_published_version for row in rows} == {2}
            assert {row._workflow_current_published_subject_declaration for row in rows} == {Workflow._meta.label_lower}
            assert {row._workflow_publication_status for row in rows} == {WorkflowStatus.PUBLISHED}

        second.archive()
        retired = Workflow.objects.with_lineage_projection().get(pk=first.pk)
        assert retired._workflow_current_published_pk is None
        assert retired._workflow_publication_status == WorkflowStatus.ARCHIVED


@pytest.mark.django_db(transaction=True)
def test_graphql_projects_lineage_context_for_head_and_version(workflow_tables: None) -> None:
    """The optimizer merges all lineage projection fields with public-id/null semantics."""

    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("workflow-lineage-projection-admin")
    with system_context(reason="test graphql workflow lineage projection"):
        head = create_workflow("GraphQL projected lineage")
        create_entry(head)
        version = head.publish()

    document = """
      query LineageProjection($head: String!, $version: String!) {
        head: workflows_by_pk(id: $head) {
          lineage_id publication_status current_published_id
          current_published_version current_published_subject_declaration
        }
        version: workflows_by_pk(id: $version) {
          lineage_id publication_status current_published_id
          current_published_version current_published_subject_declaration
        }
      }
    """
    current = result_data(execute_schema(schema, document, {"head": head.sqid, "version": version.sqid}, user=admin))
    assert (
        current["head"]
        == current["version"]
        == {
            "lineage_id": head.sqid,
            "publication_status": WorkflowStatus.PUBLISHED,
            "current_published_id": version.sqid,
            "current_published_version": 1,
            "current_published_subject_declaration": "",
        }
    )

    with system_context(reason="test graphql workflow retirement"):
        version.archive()
    retired = result_data(execute_schema(schema, document, {"head": head.sqid, "version": version.sqid}, user=admin))
    assert retired["head"]["publication_status"] == WorkflowStatus.ARCHIVED
    assert retired["head"]["current_published_id"] is None
    assert retired["version"]["current_published_id"] is None


@pytest.mark.django_db(transaction=True)
def test_graphql_wait_fields_are_nullable_for_completed_and_legacy_rows(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Fresh completed and legacy blank journals serialize truthful nullable wait context."""

    del workflow_engine_tables, no_workflow_queue
    schema = _console_schema()
    admin = _platform_admin("workflow-wait-projection-admin")
    workflow = workflow_with_steps(
        name="Wait projection",
        steps=({"key": "done", "step_class": "agent_session", "config": {}},),
        edges=(),
    )
    run = start_run(workflow)
    row = step_run_for(run, "done")
    with system_context(reason="test completed wait projection row"):
        row.mark_started()
        row.mark_succeeded(outcome="done")
        run.mark_running()
        run.mark_succeeded()

    result = result_data(
        execute_schema(
            schema,
            """
              query WaitProjection($run: String!, $row: String!) {
                run: workflow_runs_by_pk(id: $run) { origin waiting_kind next_wake_at }
                row: workflow_step_runs_by_pk(id: $row) { status waiting_kind }
              }
            """,
            {"run": run.sqid, "row": row.sqid},
            user=admin,
        )
    )
    assert result["run"] == {"origin": "MANUAL", "waiting_kind": None, "next_wake_at": None}
    assert result["row"]["status"] == "SUCCEEDED"
    assert result["row"]["waiting_kind"] is None


@pytest.mark.django_db(transaction=True)
def test_graphql_filters_workflow_runs_by_workflow_purpose(workflow_tables: None) -> None:
    """Run resource filters expose the workflow purpose owned by the related definition."""

    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("workflow-run-purpose-filter-admin")
    with system_context(reason="test workflow run purpose resource filter"):
        automation = create_workflow("Automation run filter")
        automation.purpose = WorkflowPurpose.AUTOMATION
        automation.save()
        agent_session = create_workflow("Agent session run filter")
        agent_session.purpose = WorkflowPurpose.AGENT_SESSION
        agent_session.save()
        automation_run = WorkflowRun.objects.create(workflow=automation)
        WorkflowRun.objects.create(workflow=agent_session)

    data = result_data(
        execute_schema(
            schema,
            """
              query RunsByPurpose($where: workflow_runs_bool_exp) {
                workflow_runs(where: $where) { id }
                workflow_runs_aggregate(where: $where) { aggregate { count } }
              }
            """,
            {"where": {"workflow__purpose": {"_eq": WorkflowPurpose.AUTOMATION}}},
            user=admin,
        )
    )

    assert data["workflow_runs"] == [{"id": automation_run.sqid}]
    assert data["workflow_runs_aggregate"]["aggregate"]["count"] == 1


@pytest.mark.django_db(transaction=True)
def test_graphql_filters_workflow_runs_across_a_public_workflow_lineage(workflow_tables: None) -> None:
    """A public head ID matches runs pinned to the head and all of its published revisions."""

    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("workflow-run-lineage-filter-admin")
    with system_context(reason="test workflow run lineage resource filter"):
        head = create_workflow("Run lineage filter")
        create_entry(head)
        first = head.publish()
        second = head.publish()
        expected = [
            WorkflowRun.objects.create(workflow=head),
            WorkflowRun.objects.create(workflow=first),
            WorkflowRun.objects.create(workflow=second),
        ]
        unrelated = create_workflow("Unrelated run lineage")
        WorkflowRun.objects.create(workflow=unrelated)

    where = {
        "_or": [
            {"workflow": {"_eq": head.sqid}},
            {"workflow__published_from": {"_eq": head.sqid}},
        ]
    }
    data = result_data(
        execute_schema(
            schema,
            """
              query RunsByLineage($where: workflow_runs_bool_exp) {
                workflow_runs(where: $where) { id }
                workflow_runs_aggregate(where: $where) { aggregate { count } }
              }
            """,
            {"where": where},
            user=admin,
        )
    )

    assert {row["id"] for row in data["workflow_runs"]} == {run.sqid for run in expected}
    assert data["workflow_runs_aggregate"]["aggregate"]["count"] == 3


@pytest.mark.django_db(transaction=True)
def test_workflow_step_operations_are_registry_derived_and_admin_only(
    workflow_tables: None,
    settings: Any,
) -> None:
    """The console projects generic choices plus workflow contracts from the impl field."""

    del workflow_tables
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        **settings.ANGEE_WORKFLOW_STEP_CLASSES,
        "contract_probe": "tests.test_workflows.ContractProbeStep",
    }
    schema = _console_schema()
    plain = User.objects.create_user(username="workflow-operation-plain")
    admin = _platform_admin("workflow-operation-admin")
    query = """
      query {
        workflow_step_operations {
          key label category defaults config_schema
          description selectable input_schema output_schema
          input_contract {
            raw_schema root_node_id
            nodes { id kind json_type title description nullable }
            edges { parent_node_id child_node_id kind key }
          }
          output_contract {
            raw_schema root_node_id
            nodes { id kind json_type title description nullable }
            edges { parent_node_id child_node_id kind key }
          }
          outcomes { key label description }
          effect effect_description idempotent subject_declaration
        }
      }
    """

    assert execute_schema(schema, query, user=plain).errors is not None
    operations = result_data(execute_schema(schema, query, user=admin))["workflow_step_operations"]
    assert [operation["key"] for operation in operations] == sorted(operation["key"] for operation in operations)
    by_key = {operation["key"]: operation for operation in operations}

    assert "HANDLER" in schema._schema.get_type("WorkflowStepImpl").values
    assert by_key["handler"]["selectable"] is False
    assert by_key["handler"]["effect"] == "UNKNOWN"
    assert by_key["handler"]["idempotent"] is None
    assert by_key["gate"]["idempotent"] is None
    assert by_key["map"]["effect"] == "UNKNOWN"
    assert by_key["map"]["idempotent"] is None
    probe = by_key["contract_probe"]
    assert probe["defaults"] == {"config": {"mode": "safe"}}
    assert probe["config_schema"]["properties"]["mode"]["defaultValue"] == "safe"
    assert probe["input_schema"]["required"] == ["payload"]
    assert probe["output_schema"]["required"] == ["accepted"]
    assert probe["input_contract"]["raw_schema"] == probe["input_schema"]
    assert probe["input_contract"]["root_node_id"] == 0
    assert probe["input_contract"]["nodes"][0]["kind"] == "object"
    assert probe["input_contract"]["edges"][0]["key"] == "payload"
    assert probe["output_contract"]["raw_schema"] == probe["output_schema"]
    assert probe["outcomes"] == [{"key": "accepted", "label": "Accepted", "description": "The payload was accepted."}]
    assert probe["effect"] == "UNKNOWN"
    assert probe["idempotent"] is None
    assert probe["subject_declaration"] == "tests.workflow"
    assert "archive_probe" in by_key
    assert by_key["agent"]["outcomes"] == [
        {"key": "completed", "label": "Completed", "description": ""},
        {"key": "failed", "label": "Failed", "description": ""},
    ]
    assert by_key["agent"]["effect"] == "EXTERNAL"
    assert by_key["agent"]["idempotent"] is False
    assert by_key["agent_session"]["selectable"] is False


@pytest.mark.django_db(transaction=True)
def test_workflow_step_config_query_projects_legacy_and_preserves_invalid_raw_values(
    workflow_tables: None,
) -> None:
    """The console receives canonical legacy config or explicit repair diagnostics."""

    del workflow_tables
    admin = _platform_admin("workflow-config-projection-admin")
    with system_context(reason="test workflow config GraphQL projection"):
        workflow = Workflow.objects.create(name="Config projection")
        step = Step.objects.create(
            workflow=workflow,
            key="gate",
            name="Gate",
            step_class="gate",
            config={"action": "approve", "slots": [{"assignee": "auth/user:1"}]},
        )
        models.QuerySet.update(
            Step._base_manager.filter(pk=step.pk), config={"action": "approve", "slots": [{"assignee": "auth/user:1"}]}
        )
    query = """
      query StepConfig($id: String!) {
        workflow_steps_by_pk(id: $id) { config config_errors }
      }
    """
    valid = result_data(execute_schema(_console_schema(), query, {"id": step.sqid}, user=admin))
    assert valid["workflow_steps_by_pk"]["config"]["slots"][0]["assignees"] == ["auth/user:1"]
    assert valid["workflow_steps_by_pk"]["config_errors"] == {}

    invalid = {"action": "approve", "slots": [{"assignee": ""}]}
    with system_context(reason="test invalid workflow config GraphQL projection"):
        models.QuerySet.update(Step._base_manager.filter(pk=step.pk), config=invalid)
    projected = result_data(execute_schema(_console_schema(), query, {"id": step.sqid}, user=admin))
    assert projected["workflow_steps_by_pk"]["config"] == invalid
    assert "config.slots.0.assignees.0" in projected["workflow_steps_by_pk"]["config_errors"]

    with system_context(reason="test scalar workflow config GraphQL projection"):
        models.QuerySet.update(Step._base_manager.filter(pk=step.pk), config="invalid root")
    projected = result_data(execute_schema(_console_schema(), query, {"id": step.sqid}, user=admin))
    assert projected["workflow_steps_by_pk"]["config"] == "invalid root"
    assert projected["workflow_steps_by_pk"]["config_errors"] == {"config": ["Step config must be a JSON object."]}
    step.refresh_from_db()
    assert step.config == "invalid root"


@pytest.mark.django_db(transaction=True)
def test_purpose_is_copied_as_immutable_version_content(workflow_tables: None) -> None:
    """Changing a head purpose affects a new publication, not history."""

    with system_context(reason="test workflow purpose versions"):
        head = create_workflow("Purpose versions")
        create_entry(head)
        head.purpose = WorkflowPurpose.AGENT_SESSION
        head.save()
        first = head.publish()
        head.purpose = WorkflowPurpose.AUTOMATION
        head.save()
        second = head.publish()

    first.refresh_from_db()
    assert first.purpose == WorkflowPurpose.AGENT_SESSION
    assert second.purpose == WorkflowPurpose.AUTOMATION


@pytest.mark.django_db(transaction=True)
def test_workflow_key_is_unique_per_assigned_head_and_allows_legacy_backfill(
    workflow_tables: None,
) -> None:
    """Assigned stable keys are unique and a migration-era empty key initializes once."""

    with system_context(reason="test workflow stable key uniqueness"):
        first = create_workflow("First lineage")
        with pytest.raises(ValidationError) as duplicate:
            Workflow.objects.create(key=first.key, name="Duplicate lineage")
        duplicate_error = duplicate.value.error_dict["key"][0]
        assert duplicate_error.message == "A workflow with this key already exists."
        assert duplicate_error.code == "unique"

        legacy = Workflow.objects.create(name="Legacy lineage")
        create_entry(legacy)
        published = legacy.publish()
        published_updated_at = published.updated_at
        legacy.key = "Legacy-Lineage"
        legacy.save()
        published.refresh_from_db()
        assert legacy.key == "legacy-lineage"
        assert published.key == legacy.key
        assert published.updated_at > published_updated_at

        legacy.key = "changed-lineage"
        with pytest.raises(ValidationError, match="immutable once assigned"):
            legacy.save()


@pytest.mark.django_db(transaction=True)
def test_workflow_version_rejects_a_stable_key_different_from_its_head(
    workflow_tables: None,
) -> None:
    """A version cannot validate with a stable key outside its lineage."""

    with system_context(reason="test workflow version stable key mismatch"):
        head = create_workflow("Stable head")
        mismatched = Workflow(key="other-key", name="Mismatched", published_from=head)

        with pytest.raises(ValidationError, match="share their lineage stable key"):
            mismatched.full_clean()


@pytest.mark.django_db(transaction=True)
def test_workflow_stable_key_propagation_rejects_conflicting_versions_atomically(
    workflow_tables: None,
) -> None:
    """A conflicting historical version aborts assignment and rolls back the head."""

    with system_context(reason="test workflow stable key propagation conflict"):
        head = Workflow.objects.create(name="Legacy conflict")
        create_entry(head)
        published = head.publish()
        Workflow._base_manager.filter(pk=published.pk).update(key="conflicting-key")

        head.key = "assigned-key"
        with pytest.raises(ValidationError, match="disagree with their lineage stable key"):
            head.save()

        head.refresh_from_db()
        published.refresh_from_db()
        assert head.key == ""
        assert published.key == "conflicting-key"


@pytest.mark.django_db(transaction=True)
def test_console_can_publish_workflow(workflow_tables: None) -> None:
    """The console exposes the workflow publish model method as an action."""

    del workflow_tables
    # Building the console schema resolves the whole runtime model family; the
    # concrete test classes live in test_workflows_engine (function-level import
    # because that module imports this one for the definition models).
    schema = _console_schema()
    with system_context(reason="test workflows graphql publish"):
        workflow = create_workflow()
        create_entry(workflow)
    admin = _platform_admin("workflow-publish-admin")

    result = result_data(
        execute_schema(
            schema,
            """
            mutation Publish($id: ID!) {
              publish_workflow(workflow: $id) { ok message }
            }
            """,
            {"id": workflow.sqid},
            user=admin,
        )
    )["publish_workflow"]

    assert result["ok"] is True
    with system_context(reason="test workflows graphql publish result"):
        published = Workflow.objects.get(published_from=workflow)
    assert published.status == WorkflowStatus.PUBLISHED


@pytest.mark.django_db(transaction=True)
def test_console_rejects_reassigning_an_existing_workflow_stable_key(
    workflow_tables: None,
) -> None:
    """The update API delegates assignment-once enforcement to Workflow.save()."""

    del workflow_tables
    schema = _console_schema()
    admin = _platform_admin("workflow-key-admin")
    with system_context(reason="test workflow stable key update API"):
        workflow = create_workflow("API stable key")

    result = execute_schema(
        schema,
        """
        mutation ReassignWorkflowKey($id: String!) {
          update_workflows_by_pk(pk_columns: {id: $id}, _set: {key: "another-key"}) { key }
        }
        """,
        {"id": workflow.sqid},
        user=admin,
    )

    assert result.errors is not None
    assert "immutable once assigned" in result.errors[0].message
    with system_context(reason="test workflow stable key update API result"):
        workflow.refresh_from_db()
    assert workflow.key == "api-stable-key"


@pytest.mark.django_db(transaction=True)
def test_workflows_for_subject_declaration_filters_resource_and_rebac(workflow_tables: None) -> None:
    """The subject declaration resolver returns only current workflows the actor may start."""

    del workflow_tables
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-subject-owner")
    outsider = User.objects.create_user(username="workflow-subject-outsider")
    other_owner = User.objects.create_user(username="workflow-subject-other-owner")
    _published_workflow(name="Matching", subject_declaration=Workflow._meta.label, owner=owner)
    _published_workflow(name="Any subject", subject_declaration="", owner=owner)
    _published_workflow(name="Wrong resource", subject_declaration=Step._meta.label, owner=owner)
    _published_workflow(
        name="Agent session",
        subject_declaration=Workflow._meta.label,
        owner=owner,
        purpose=WorkflowPurpose.AGENT_SESSION,
    )
    _published_workflow(name="Hidden matching", subject_declaration=Workflow._meta.label, owner=other_owner)

    query = """
      query WorkflowsForSubjectDeclaration($subjectDeclaration: String!) {
        workflows_for_subject_declaration(subject_declaration: $subjectDeclaration) {
          id
          key
          name
          subject_declaration
        }
      }
    """
    with CaptureQueriesContext(connection) as queries:
        visible = result_data(
            execute_schema(
                schema,
                query,
                {"subjectDeclaration": Workflow._meta.label},
                user=owner,
            )
        )["workflows_for_subject_declaration"]
    hidden = result_data(
        execute_schema(
            schema,
            query,
            {"subjectDeclaration": Workflow._meta.label},
            user=outsider,
        )
    )["workflows_for_subject_declaration"]

    assert [(row["key"], row["name"], row["subject_declaration"]) for row in visible] == [
        ("any-subject", "Any subject", ""),
        ("matching", "Matching", Workflow._meta.label_lower),
    ]
    workflow_selects = [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].lstrip().upper().startswith("SELECT")
        and Workflow._meta.db_table in query["sql"]
    ]
    # One REBAC identity projection and one annotated domain query, independent
    # of the number of workflows returned; lineage fields add no per-row reads.
    assert len(workflow_selects) == 2
    assert hidden == []


@pytest.mark.django_db(transaction=True)
def test_for_subject_declaration_returns_only_current_versions(workflow_tables: None) -> None:
    """Version currency holds set-wide: newest published wins, a newer archive retires."""

    with system_context(reason="test workflows subject declaration currency"):
        owner = User.objects.create_user(username="workflow-currency-owner")
        draft, _first = _published_workflow(
            name="Currency",
            subject_declaration=Workflow._meta.label,
            owner=owner,
        )
        second = draft.publish()

        current = [
            row.pk
            for row in Workflow.objects.for_subject_declaration(Workflow._meta.label)
            if row.published_from_id == draft.pk
        ]
        assert current == [second.pk]

        second.archive()
        retired = [
            row
            for row in Workflow.objects.for_subject_declaration(Workflow._meta.label)
            if row.published_from_id == draft.pk
        ]
        assert retired == []


@pytest.mark.django_db(transaction=True)
def test_start_workflow_run_starts_subject_and_enforces_rebac(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """The Run workflow mutation starts for an owner and refuses another actor."""

    del workflow_engine_tables, no_workflow_queue
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-start-owner")
    outsider = User.objects.create_user(username="workflow-start-outsider")
    subject, published = _published_workflow(
        name="Start on workflow",
        subject_declaration=Workflow._meta.label,
        owner=owner,
    )
    mutation = """
      mutation RunWorkflow($workflow: ID!, $subjectDeclaration: String!, $subjectId: ID!) {
        start_workflow_run(
          workflow: $workflow
          subject: { subject_declaration: $subjectDeclaration, id: $subjectId }
        ) {
          ok
          message
          validation_errors
          id
        }
      }
    """
    variables = {
        "workflow": published.sqid,
        "subjectDeclaration": Workflow._meta.label,
        "subjectId": subject.sqid,
    }

    started = result_data(execute_schema(schema, mutation, variables, user=owner))["start_workflow_run"]
    refused = result_data(execute_schema(schema, mutation, variables, user=outsider))["start_workflow_run"]

    assert started["ok"] is True
    assert started["id"]
    assert refused["ok"] is False
    assert refused["validation_errors"]["__all__"]
    with system_context(reason="test workflows start mutation result"):
        run = WorkflowRun.objects.get(sqid=started["id"])
        assert WorkflowRun.objects.count() == 1
    assert run.workflow == published
    assert run.subject == subject
    assert run.created_by == owner


@pytest.mark.django_db(transaction=True)
def test_start_workflow_run_requires_access_to_the_subject(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """The subject gate refuses foreign records independently of the workflow gate."""

    del workflow_engine_tables, no_workflow_queue
    schema = _console_schema()
    owner = User.objects.create_user(username="workflow-subject-owner")
    outsider = User.objects.create_user(username="workflow-subject-outsider")
    _, published = _published_workflow(
        name="Start elsewhere",
        subject_declaration=Workflow._meta.label,
        owner=owner,
    )
    with system_context(reason="test workflows foreign subject"):
        foreign = Workflow.objects.create(
            name="Foreign subject",
            created_by=outsider,
            updated_by=outsider,
        )
    mutation = """
      mutation RunWorkflow($workflow: ID!, $subjectDeclaration: String!, $subjectId: ID!) {
        start_workflow_run(
          workflow: $workflow
          subject: { subject_declaration: $subjectDeclaration, id: $subjectId }
        ) {
          ok
          validation_errors
        }
      }
    """
    refused = result_data(
        execute_schema(
            schema,
            mutation,
            {
                "workflow": published.sqid,
                "subjectDeclaration": Workflow._meta.label,
                "subjectId": foreign.sqid,
            },
            user=owner,
        )
    )["start_workflow_run"]

    assert refused["ok"] is False
    assert refused["validation_errors"]["subject"]
    with system_context(reason="test workflows foreign subject count"):
        assert WorkflowRun.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_current_published_resolution_uses_lineage_head(workflow_tables: None) -> None:
    """The manager resolves the latest published version from any row in a lineage."""

    with system_context(reason="test workflows current version"):
        draft = create_workflow()
        create_entry(draft)
        first = draft.publish()
        second = draft.publish()

        assert Workflow.objects.current_published_for(draft) == second
        assert Workflow.objects.current_published_for(first) == second
        assert Workflow.objects.current_published_for(second) == second


@pytest.mark.django_db(transaction=True)
def test_triggers_attach_to_lineage_heads_and_default_disabled(workflow_tables: None) -> None:
    """Triggers point at lineage heads and are disabled unless explicitly enabled."""

    with system_context(reason="test workflows trigger constraints"):
        draft = create_workflow()
        create_entry(draft)
        trigger = Trigger.objects.create(workflow=draft, kind=TriggerKind.MANUAL)
        published = draft.publish()

        assert trigger.enabled is False

        with pytest.raises(ValidationError, match="lineage head"):
            Trigger.objects.create(workflow=published, kind=TriggerKind.SCHEDULE)
