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
from tests.workflows import Edge, Step, Trigger, Workflow

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
    importlib.import_module("tests.test_workflows_engine")
    workflows_schema = importlib.import_module("angee.workflows.schema")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")
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
