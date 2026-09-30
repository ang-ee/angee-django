"""Contracts for the reusable Django test composition."""

from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from rebac import actor_context, system_context
from rebac.checks import check_index_ready
from rebac.field_backing import field_backing_model_errors
from rebac.schema import parse_zed, resolve_schema_path

from angee.integrate.testing import models as integrate_models
from angee.testing.permissions import bind_test_permission_schemas
from angee.workflows import models as workflow_sources
from angee.workflows.testing import models as workflow_models
from angee.workflows.testing.models import Workflow, WorkflowRun, WorkflowVersion
from tests.conftest import make_addon


def test_partial_permission_schema_keeps_concrete_backing_errors(tmp_path: Path) -> None:
    """Absent model definitions are omitted; malformed concrete policy stays loud."""

    config = make_addon(path=tmp_path / "addon")
    (Path(config.path) / "permissions.zed").write_text(
        """definition tests/absent {
    relation owner: auth/user // rebac:field=owner
}
definition tests/virtual {
    relation member: auth/user
}
definition workflows/workflow {
    relation owner: auth/user // rebac:field=missing_owner
}
""",
        encoding="utf-8",
    )

    bind_test_permission_schemas([config], tmp_path / "permissions")

    source = resolve_schema_path(config)
    assert source is not None
    schema = parse_zed(source.read_text(encoding="utf-8"))
    assert schema.get_definition("tests/absent") is None
    assert schema.get_definition("tests/virtual") is not None
    definition = schema.get_definition("workflows/workflow")
    assert definition is not None
    errors = field_backing_model_errors(definition, definition.relations[0])
    assert errors and "missing_owner" in errors[0]


def test_permission_sync_builds_the_native_index(composed_tables: None) -> None:
    """Native schema sync leaves fresh test databases ready for permission reads."""

    assert check_index_ready(databases=["default"]) == []
    call_command("rebac", "index", "verify", verbosity=0)


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


@pytest.mark.parametrize("model", (Workflow, WorkflowRun))
def test_shared_models_preserve_source_grant_contract(model) -> None:
    """Reusable compositions preserve all source model share declarations."""

    source = getattr(workflow_sources, model.__name__)
    assert model.get_rebac_grantable() == source.get_rebac_grantable()


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
