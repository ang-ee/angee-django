"""Tests for MCP bearer identity resolution."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from fastmcp.exceptions import ToolError
from rebac import SubjectRef, actor_context, system_context, to_object_ref
from rebac.backends import backend

from angee.agents.mcp_verifier import resolve_actor
from angee.integrate.credentials import CredentialKind
from angee.mcp.graphql import _CompiledTool
from tests.conftest import IAM_CONNECTION_TEST_MODELS, INTEGRATE_TEST_MODELS, Credential, _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, MCPServer, MCPTool, User
from tests.test_integrate_vcs import VCS_TEST_MODELS


def _test_actor_user_resolver(subject_id: str) -> int | None:
    """Resolver used by the MCP species-gate test."""

    return 123 if subject_id == "agent-1" else None


@pytest.fixture()
def agents_console_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete agents tables needed by the MCP verifier tests."""

    del transactional_db
    models = IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS + AGENTS_GRAPHQL_MODELS
    created = _create_tables(models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_mcp_bearer_resolves_to_single_agent_principal(agents_console_tables: None) -> None:
    """A bearer attached to exactly one rendered agent runs as that agent."""

    owner = User.objects.create_user(username="mcp-agent-owner", email="mcp-agent@example.com")
    with system_context(reason="test.mcp.actor.single_agent"):
        credential = Credential.objects.create_local_credential(
            owner,
            kind=str(CredentialKind.STATIC_TOKEN),
            name="mcp-bearer",
            material={"api_key": "tok-agent"},
        )
        server = MCPServer.objects.create(name="notes", url="http://x/mcp/notes/", credential=credential)
        agent = Agent.objects.create(name="MCP Agent", owner=owner)
        agent.mark_provisioning()
        agent.mark_provisioned(workspace="ws-mcp-agent", service="svc-mcp-agent")
        agent.mcp_servers.add(server)

    assert resolve_actor("tok-agent") == SubjectRef.of("agents/agent", str(agent.sqid))


def test_mcp_bearer_shared_by_multiple_agents_fails_closed(agents_console_tables: None) -> None:
    """A server-level bearer selected by multiple agents is ambiguous."""

    owner = User.objects.create_user(username="mcp-shared-owner", email="mcp-shared@example.com")
    with system_context(reason="test.mcp.actor.ambiguous"):
        credential = Credential.objects.create_local_credential(
            owner,
            kind=str(CredentialKind.STATIC_TOKEN),
            name="shared-mcp-bearer",
            material={"api_key": "tok-shared"},
        )
        server = MCPServer.objects.create(name="shared", url="http://x/mcp/shared/", credential=credential)
        first = Agent.objects.create(name="First MCP Agent", owner=owner)
        second = Agent.objects.create(name="Second MCP Agent", owner=owner)
        first.mark_provisioning()
        first.mark_provisioned(workspace="ws-first", service="svc-first")
        second.mark_provisioning()
        second.mark_provisioned(workspace="ws-second", service="svc-second")
        first.mcp_servers.add(server)
        second.mcp_servers.add(server)

    assert resolve_actor("tok-shared") is None
    assert resolve_actor("wrong-token") is None
    assert resolve_actor("") is None


def test_mcp_bearer_with_zero_attached_agents_fails_closed(agents_console_tables: None) -> None:
    """A server credential with no selected agent resolves to no actor."""

    owner = User.objects.create_user(username="mcp-zero-owner", email="mcp-zero@example.com")
    with system_context(reason="test.mcp.actor.zero_agents"):
        credential = Credential.objects.create_local_credential(
            owner,
            kind=str(CredentialKind.STATIC_TOKEN),
            name="zero-agent-mcp-bearer",
            material={"api_key": "tok-zero"},
        )
        MCPServer.objects.create(name="zero", url="http://x/mcp/zero/", credential=credential)

    assert resolve_actor("tok-zero") is None


@pytest.mark.parametrize(
    ("agent_kwargs", "mark_provisioned"),
    [
        ({"is_template": True}, True),
        ({}, False),
    ],
)
def test_mcp_bearer_ignores_template_and_unprovisioned_agents(
    agents_console_tables: None,
    agent_kwargs: dict[str, Any],
    mark_provisioned: bool,
) -> None:
    """Only concrete provisioned agents can act through an MCP bearer."""

    owner = User.objects.create_user(
        username=f"mcp-filter-owner-{mark_provisioned}",
        email=f"filter-{mark_provisioned}@example.com",
    )
    token = f"tok-filter-{mark_provisioned}"
    with system_context(reason="test.mcp.actor.filtered"):
        credential = Credential.objects.create_local_credential(
            owner,
            kind=str(CredentialKind.STATIC_TOKEN),
            name=f"filtered-mcp-bearer-{mark_provisioned}",
            material={"api_key": token},
        )
        server = MCPServer.objects.create(
            name=f"filtered-{mark_provisioned}",
            url="http://x/mcp/filter/",
            credential=credential,
        )
        agent = Agent.objects.create(name=f"Filtered {mark_provisioned}", owner=owner, **agent_kwargs)
        if mark_provisioned:
            agent.mark_provisioning()
            agent.mark_provisioned(
                workspace=f"ws-filtered-{mark_provisioned}",
                service=f"svc-filtered-{mark_provisioned}",
            )
        agent.mcp_servers.add(server)

    assert resolve_actor(token) is None


def test_agent_mcp_m2m_reconciles_rebac_read_tuples(agents_console_tables: None) -> None:
    """Agent MCP server/tool selections grant and revoke the matching REBAC tuples."""

    owner = User.objects.create_user(username="mcp-rebac-owner", email="mcp-rebac@example.com")
    with system_context(reason="test.mcp.rebac.seed"):
        server = MCPServer.objects.create(name="rebac-server", url="http://x/mcp/rebac/")
        tool = MCPTool.objects.create(server=server, name="rebac-tool")
        agent = Agent.objects.create(name="REBAC Agent", owner=owner)
        agent.mark_provisioning()
        agent.mark_provisioned(workspace="ws-rebac", service="svc-rebac")
        subject = agent.principal_subject()
        server_ref = to_object_ref(server)
        tool_ref = to_object_ref(tool)

        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.add(server)
        assert backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.remove(server)
        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed

        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        agent.mcp_tools.add(tool)
        assert backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        agent.mcp_tools.remove(tool)
        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed


def test_requires_user_actor_is_actor_species_not_attribution_user() -> None:
    """Service-user attribution must not turn an agent actor into a user actor."""

    tool = _CompiledTool(
        name="requires_user",
        description="requires user",
        parameters={"type": "object", "properties": {}},
        output_schema={"type": "object"},
        schema_name="public",
        document="query { noop }",
        payload_field="noop",
        node_type="Noop",
        is_list=False,
        leaves=(),
        requires_user_actor=True,
    )

    with (
        override_settings(
            ANGEE_ACTOR_USER_RESOLVERS={"agents/agent": "tests.test_mcp._test_actor_user_resolver"}
        ),
        actor_context(SubjectRef.of("agents/agent", "agent-1")),
        pytest.raises(ToolError, match="user actor"),
    ):
        asyncio.run(tool.run({}))
