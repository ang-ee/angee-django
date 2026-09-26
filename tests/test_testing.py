"""Contracts for the reusable Django test composition."""

import pytest
from django.apps import apps
from django.db import connection
from rebac import system_context

from angee.integrate.testing import models as integrate_models
from angee.workflows import models as workflow_sources
from angee.workflows.testing import models as workflow_models
from angee.workflows.testing.models import Decision, Workflow, WorkflowRun


@pytest.mark.parametrize("shared_models", (integrate_models, workflow_models))
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


@pytest.mark.parametrize("model", (Workflow, WorkflowRun, Decision))
def test_shared_models_preserve_source_grant_contract(model) -> None:
    """Reusable compositions preserve all source model share declarations."""

    source = getattr(workflow_sources, model.__name__)
    assert model.get_rebac_grantable() == source.get_rebac_grantable()
