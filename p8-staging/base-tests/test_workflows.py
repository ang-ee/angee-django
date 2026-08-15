"""Tests for workflow definition models."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.workflows.models import (
    TriggerKind,
    WorkflowStatus,
)
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.workflows import Edge, Step, Trigger, Workflow, WorkflowRun

User = get_user_model()
pytest_plugins = ("tests.workflows",)


def create_workflow(name: str = "Document Review") -> Workflow:
    """Create one draft workflow in the test table."""

    return Workflow.objects.create(name=name)


def create_entry(workflow: Workflow, *, key: str = "start", name: str = "Start") -> Step:
    """Create one entry step for ``workflow``."""

    return Step.objects.create(workflow=workflow, key=key, name=name, is_entry=True)


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


def _published_workflow(*, name: str, subject_declaration: str, owner: Any) -> tuple[Workflow, Workflow]:
    """Seed one actor-owned workflow lineage and publish its first version."""

    with system_context(reason="test workflows subject declaration definition"):
        draft = Workflow.objects.create(
            name=name,
            subject_declaration=subject_declaration,
            created_by=owner,
            updated_by=owner,
        )
        create_entry(draft)
        return draft, draft.publish()


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
        finish = Step.objects.create(workflow=draft, key="finish", name="Finish")
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
        assert first.version == 1
        assert first.name == "Document Review"
        assert Step.objects.get(workflow=first, key="start").name == "Start"
        assert Edge.objects.filter(workflow=first, condition="done").count() == 1

        assert second.version == 2
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
    _published_workflow(name="Hidden matching", subject_declaration=Workflow._meta.label, owner=other_owner)

    query = """
      query WorkflowsForSubjectDeclaration($subjectDeclaration: String!) {
        workflows_for_subject_declaration(subject_declaration: $subjectDeclaration) {
          id
          name
          subject_declaration
        }
      }
    """
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

    assert [(row["name"], row["subject_declaration"]) for row in visible] == [
        ("Any subject", ""),
        ("Matching", Workflow._meta.label_lower),
    ]
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
