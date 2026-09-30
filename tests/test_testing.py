"""Contracts for the reusable Django test composition."""

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from rebac import actor_context, system_context

from angee.integrate.testing import models as integrate_models
from angee.workflows import models as workflow_sources
from angee.workflows.testing import models as workflow_models
from angee.workflows.testing.models import Workflow, WorkflowRun, WorkflowVersion
from tests import decisions_models


@pytest.mark.parametrize("shared_models", (integrate_models, workflow_models, decisions_models))
def test_native_database_setup_creates_shared_model_tables(transactional_db: None, shared_models) -> None:
    """Shared tables exist without requesting the permission-sync fixture."""

    tables = {
        model._meta.db_table
        for model in apps.get_models()
        if model.__module__ == shared_models.__name__
    }

    assert tables
    assert tables <= set(connection.introspection.table_names())


@pytest.mark.parametrize("case", ("first", "second"))
def test_native_database_cleanup_isolates_shared_models(transactional_db: None, case: str) -> None:
    """Each native transactional fixture starts with no prior shared-model row."""

    with system_context(reason="test native database isolation"):
        assert not Workflow.objects.filter(key="native-table-isolation").exists()
        workflow = Workflow.objects.create(key="native-table-isolation", name=f"Native isolation {case}")
        assert Workflow.objects.get(pk=workflow.pk).name == f"Native isolation {case}"


@pytest.mark.parametrize("model", (Workflow, WorkflowRun))
def test_shared_models_preserve_source_grant_contract(model) -> None:
    """Reusable compositions preserve all source model share declarations."""

    source = getattr(workflow_sources, model.__name__)
    assert model.get_rebac_grantable() == source.get_rebac_grantable()


def test_decision_resources_register_once_from_central_models() -> None:
    """Decision tests use one concrete registration independent of workflow test models."""
    assert {model.__name__ for model in apps.get_models() if model._meta.app_label == "decisions"} == {
        "Decision", "DecisionEvidence", "DecisionGroup",
    }
    assert all(
        apps.get_model("decisions", name).__module__ == decisions_models.__name__
        for name in ("Decision", "DecisionEvidence", "DecisionGroup")
    )


def test_workflow_run_reads_through_the_version_workflow_relation(composed_tables: None) -> None:
    """Forward FK paths inherit workflow access without copying its foreign key."""

    with system_context(reason="workflow forward relation fixture"):
        owner = get_user_model().objects.create_user(username="workflow-owner")
        runner = get_user_model().objects.create_user(username="workflow-runner")
        workflow = Workflow.objects.create(key="forward-relation", name="Forward relation", created_by=owner)
        version = WorkflowVersion.objects.create(workflow=workflow, number=1, document={}, content_hash="0" * 64)
        run = WorkflowRun.objects.create(version=version, run_as=runner)
    with actor_context(owner):
        assert WorkflowRun.objects.filter(pk=run.pk).exists()
