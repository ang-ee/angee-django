"""Tests for MCP bearer identity resolution."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context, to_object_ref
from rebac.backends import backend

from angee.agents.grants import tool_grant_ref
from angee.agents.mcp_verifier import resolve_actor
from angee.agents.models import MCPPlacement
from angee.integrate.credentials import CredentialKind
from tests.conftest import IAM_CONNECTION_TEST_MODELS, INTEGRATE_TEST_MODELS, Credential, _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, MCPServer, MCPTool, User
from tests.test_integrate_vcs import VCS_TEST_MODELS


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


def _static_credential(owner: User, *, name: str, token: str) -> Any:
    """Create a local static-token credential holding ``token`` for MCP verifier tests."""

    return Credential.objects.create_local_credential(
        owner, kind=str(CredentialKind.STATIC_TOKEN), name=name, material={"api_key": token}
    )


def _provisioned_agent(owner: User, *, name: str, workspace: str = "ws", service: str = "svc", **kwargs: Any) -> Any:
    """Create a READY/RUNNING agent, mirroring the operator's provision transitions."""

    agent = Agent.objects.create(name=name, owner=owner, **kwargs)
    agent.mark_provisioning()
    agent.mark_provisioned(workspace=workspace, service=service)
    return agent


def test_mcp_bearer_resolves_to_single_agent_principal(agents_console_tables: None) -> None:
    """An agent presenting its own derived bearer for an internal server runs as that agent."""

    owner = User.objects.create_user(username="mcp-agent-owner", email="mcp-agent@example.com")
    with system_context(reason="test.mcp.actor.single_agent"):
        credential = _static_credential(owner, name="mcp-bearer", token="tok-agent")
        server = MCPServer.objects.create(
            name="notes", url="http://x/mcp/notes/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="MCP Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert bearer.startswith(f"{agent.sqid}.")
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_declines_agent_without_service_user(agents_console_tables: None) -> None:
    """Legacy runnable rows without a service principal fail closed."""

    owner = User.objects.create_user(username="mcp-missing-user-owner")
    with system_context(reason="test.mcp.actor.missing_service_user"):
        credential = _static_credential(owner, name="mcp-missing-user", token="tok-missing-user")
        server = MCPServer.objects.create(
            name="missing-user",
            url="http://x/mcp/missing-user/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = _provisioned_agent(owner, name="Missing User Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)
        Agent._base_manager.filter(pk=agent.pk).update(user=None)

    assert resolve_actor(bearer) is None


def test_mcp_bearer_resolves_for_ready_in_process_agent_without_operator_names(
    agents_console_tables: None,
) -> None:
    """A READY/RUNNING in-process agent authenticates despite rendering no workspace or service."""

    owner = User.objects.create_user(username="mcp-process-owner", email="mcp-process@example.com")
    with system_context(reason="test.mcp.actor.in_process"):
        credential = _static_credential(owner, name="mcp-process-bearer", token="tok-process")
        server = MCPServer.objects.create(
            name="angee", url="http://x/mcp/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Process Agent", workspace="", service="", runtime_class="pydantic")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert agent.workspace == "" and agent.service == ""
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_distinguishes_agents_sharing_one_internal_server(agents_console_tables: None) -> None:
    """Two agents on one internal server each resolve to their own subject; a spoof is declined."""

    owner = User.objects.create_user(username="mcp-shared-owner", email="mcp-shared@example.com")
    with system_context(reason="test.mcp.actor.per_agent"):
        credential = _static_credential(owner, name="shared-mcp-bearer", token="tok-shared")
        server = MCPServer.objects.create(
            name="shared", url="http://x/mcp/shared/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        first = _provisioned_agent(owner, name="First MCP Agent", workspace="ws-first", service="svc-first")
        second = _provisioned_agent(owner, name="Second MCP Agent", workspace="ws-second", service="svc-second")
        first.mcp_servers.add(server)
        second.mcp_servers.add(server)
        first_bearer = server.bearer_for(first)
        second_bearer = server.bearer_for(second)

    # Both share one server credential yet each derived bearer resolves to its own subject.
    assert first_bearer != second_bearer
    assert resolve_actor(first_bearer) == first.principal_subject()
    assert resolve_actor(second_bearer) == second.principal_subject()
    # A spoof — the second agent's sqid carrying the first agent's digest — is declined.
    _, _, first_digest = first_bearer.partition(".")
    assert resolve_actor(f"{second.sqid}.{first_digest}") is None


def test_mcp_bearer_raw_credential_secret_does_not_authenticate(agents_console_tables: None) -> None:
    """The raw server credential secret is no longer a valid bearer; the derived one is required."""

    owner = User.objects.create_user(username="mcp-raw-owner", email="mcp-raw@example.com")
    with system_context(reason="test.mcp.actor.raw_secret"):
        credential = _static_credential(owner, name="raw-mcp-bearer", token="tok-raw")
        server = MCPServer.objects.create(
            name="raw", url="http://x/mcp/raw/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Raw MCP Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert resolve_actor("tok-raw") is None
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_with_valid_agent_and_non_ascii_digest_is_declined(agents_console_tables: None) -> None:
    """Malformed Unicode digest input declines instead of reaching compare_digest."""

    owner = User.objects.create_user(username="mcp-unicode-owner", email="mcp-unicode@example.com")
    with system_context(reason="test.mcp.actor.unicode_digest"):
        credential = _static_credential(owner, name="unicode-mcp-bearer", token="tok-unicode")
        server = MCPServer.objects.create(
            name="unicode",
            url="http://x/mcp/unicode/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = _provisioned_agent(owner, name="Unicode Digest Agent")
        agent.mcp_servers.add(server)

    assert resolve_actor(f"{agent.sqid}.{'é' * 64}") is None


@pytest.mark.parametrize("bearer", ["", "no-dot", ".", "agt_only.", ".digest-only"])
def test_mcp_bearer_malformed_is_declined(agents_console_tables: None, bearer: str) -> None:
    """A bearer that is empty or missing a non-empty sqid/digest half resolves to no actor."""

    assert resolve_actor(bearer) is None


def test_mcp_bearer_for_external_server_does_not_authenticate(agents_console_tables: None) -> None:
    """An external server verifies its own raw token, so its bearer never authenticates here."""

    owner = User.objects.create_user(username="mcp-external-owner", email="mcp-external@example.com")
    with system_context(reason="test.mcp.actor.external"):
        credential = _static_credential(owner, name="external-mcp-bearer", token="tok-external")
        server = MCPServer.objects.create(
            name="external", url="http://x/mcp/external/", credential=credential, placement=MCPPlacement.EXTERNAL
        )
        agent = _provisioned_agent(owner, name="External MCP Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    # For an external server ``bearer_for`` hands back the raw secret, which the platform
    # verifier declines: it only trusts the per-agent digest of an internal server.
    assert bearer == "tok-external"
    assert resolve_actor(bearer) is None


def test_mcp_bearer_from_server_the_agent_lacks_is_declined(agents_console_tables: None) -> None:
    """A valid agent presenting a bearer for an internal server it is not attached to is declined."""

    owner = User.objects.create_user(username="mcp-unattached-owner", email="mcp-unattached@example.com")
    with system_context(reason="test.mcp.actor.unattached_server"):
        held_credential = _static_credential(owner, name="held-mcp-bearer", token="tok-held")
        other_credential = _static_credential(owner, name="other-mcp-bearer", token="tok-other")
        held = MCPServer.objects.create(
            name="held", url="http://x/mcp/held/", credential=held_credential, placement=MCPPlacement.INTERNAL
        )
        other = MCPServer.objects.create(
            name="other", url="http://x/mcp/other/", credential=other_credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Unattached MCP Agent")
        agent.mcp_servers.add(held)
        held_bearer = held.bearer_for(agent)
        other_bearer = other.bearer_for(agent)

    assert resolve_actor(held_bearer) == agent.principal_subject()
    assert resolve_actor(other_bearer) is None


def test_mcp_bearer_for_agent_with_no_attached_servers_is_declined(agents_console_tables: None) -> None:
    """A provisioned agent attached to no internal server resolves to no actor."""

    owner = User.objects.create_user(username="mcp-zero-owner", email="mcp-zero@example.com")
    with system_context(reason="test.mcp.actor.no_servers"):
        credential = _static_credential(owner, name="zero-agent-mcp-bearer", token="tok-zero")
        server = MCPServer.objects.create(
            name="zero", url="http://x/mcp/zero/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Detached MCP Agent")
        bearer = server.bearer_for(agent)

    assert resolve_actor(bearer) is None


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
    with system_context(reason="test.mcp.actor.filtered"):
        credential = _static_credential(
            owner, name=f"filtered-mcp-bearer-{mark_provisioned}", token=f"tok-filter-{mark_provisioned}"
        )
        server = MCPServer.objects.create(
            name=f"filtered-{mark_provisioned}",
            url="http://x/mcp/filter/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = Agent.objects.create(name=f"Filtered {mark_provisioned}", owner=owner, **agent_kwargs)
        if mark_provisioned:
            agent.mark_provisioning()
            agent.mark_provisioned(
                workspace=f"ws-filtered-{mark_provisioned}",
                service=f"svc-filtered-{mark_provisioned}",
            )
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert resolve_actor(bearer) is None


def test_agent_mcp_m2m_reconciles_server_read_and_tool_use(agents_console_tables: None) -> None:
    """Server selection gates catalogue reads; tool selection gates invocation."""

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
        grant_ref = tool_grant_ref(str(server.sqid), tool.name)

        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.add(server)
        assert backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.remove(server)
        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed

        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert not backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
        agent.mcp_tools.add(tool)
        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
        agent.mcp_tools.remove(tool)
        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert not backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
