"""Workflow public identities are projected by the downstream composition addon."""

from typing import Any, cast

import pytest
import strawberry
from strawberry.schema.config import StrawberryConfig

from angee.graphql.ids import to_public_id
from angee.workflows_integrate.schema import IntegrationSyncRunExtension, WorkflowRun
from tests.conftest import Integration, VcsBridge


@pytest.fixture
def sync_schema() -> strawberry.Schema:
    @strawberry.type
    class Query:
        @strawberry.field
        def integration(self) -> IntegrationSyncRunExtension:
            return cast(IntegrationSyncRunExtension, Integration())

    return strawberry.Schema(query=Query, config=StrawberryConfig(auto_camel_case=False))


@pytest.mark.parametrize("run_id", [None, 42])
def test_sync_run_projects_public_workflow_identity(
    monkeypatch: pytest.MonkeyPatch, sync_schema: strawberry.Schema, run_id: int | None
) -> None:
    bridge = VcsBridge(sync_run_id=run_id)
    monkeypatch.setattr(Integration, "concrete_capability", lambda self: bridge)

    result = sync_schema.execute_sync("{ integration { sync_run } }")

    assert not result.errors
    assert result.data == {"integration": {"sync_run": to_public_id(WorkflowRun, run_id)}}


def test_plain_integration_has_no_sync_run(monkeypatch: pytest.MonkeyPatch, sync_schema: strawberry.Schema) -> None:
    integration = Integration()
    monkeypatch.setattr(Integration, "concrete_capability", lambda self: integration)

    result = sync_schema.execute_sync("{ integration { sync_run } }")

    assert not result.errors
    assert result.data == {"integration": {"sync_run": None}}


def test_sync_run_keeps_concrete_capability_authorization(
    monkeypatch: pytest.MonkeyPatch, sync_schema: strawberry.Schema
) -> None:
    def denied(self: Any) -> None:
        raise PermissionError("Concrete bridge is not readable.")

    monkeypatch.setattr(Integration, "concrete_capability", denied)

    result = sync_schema.execute_sync("{ integration { sync_run } }")

    assert result.errors
    assert result.errors[0].message == "Concrete bridge is not readable."
    assert result.data == {"integration": {"sync_run": None}}
