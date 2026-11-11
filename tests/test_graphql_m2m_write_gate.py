"""A GraphQL mutation that sets a many-to-many list passes REBAC's M2M edge gate.

``agents/mcp_server#selected_agent`` is backed by ``Agent.mcp_servers``. Writing that edge
needs ``mcp_server#write`` (administrators only), and the edge grants
``mcp_server#read`` to the selected agent's actor. strawberry-django saved the through
row directly when a mutation set the list, so ``m2m_changed`` never ran and the gate
was skipped; the fork backports upstream #979, which adds through the related manager.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from rebac import actor_context, system_context

from angee.agents.testing.models import Agent, MCPServer
from angee.graphql.testing.documents import execute_schema
from tests.test_agents_graphql import _schema

pytestmark = pytest.mark.django_db


def _fixture() -> tuple[Any, Any, Any, Any]:
    users = get_user_model().objects
    actor = users.create_user(username="m2m-gate-agent-actor")
    other = users.create_user(username="m2m-gate-other-actor")
    with system_context(reason="test.m2m_gate.fixture"):
        server = MCPServer.objects.create(name="private", url="http://mcp.example/private/")
        selecting = Agent.objects.create(name="Selecting", owner=actor, user=actor)
        selecting.mcp_servers.add(server)
        target = Agent.objects.create(name="Target", owner=actor, user=other)
    return actor, server, target, other


def test_manager_add_is_refused_without_server_write() -> None:
    actor, server, target, _other = _fixture()
    with actor_context(actor), pytest.raises(PermissionDenied):
        target.mcp_servers.add(server)


def test_graphql_list_update_is_refused_without_server_write() -> None:
    actor, server, target, other = _fixture()
    result = execute_schema(
        _schema(),
        "mutation($id: String!, $servers: [ID!]) {"
        " update_agents_by_pk(pk_columns: {id: $id}, _set: {mcp_servers: $servers}) { id } }",
        {"id": str(target.sqid), "servers": [str(server.sqid)]},
        user=actor,
    )
    with system_context(reason="test.m2m_gate.verify"):
        attached = target.mcp_servers.filter(pk=server.pk).exists()
    other_reads = MCPServer.objects.with_actor(other).filter(pk=server.pk).exists()
    assert result.errors, "the GraphQL update was accepted"
    assert not attached, "the server was attached without mcp_server#write"
    assert not other_reads
