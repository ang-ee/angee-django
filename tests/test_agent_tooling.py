"""End-to-end contracts for REBAC-gated native agent tools."""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connection, models
from django.test import override_settings
from fastmcp import Context, FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from pydantic_ai import RunContext
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturnPart,
    ToolSearchReturnPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets._tool_search import parse_discovered_tools
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage
from pydantic_core import to_jsonable_python
from rebac import ObjectRef, RelationshipTuple, SubjectRef, actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.relationships import write_relationships

from angee.agents.grants import tool_grant_ref
from angee.agents.models import ToolGrant, ToolRole
from angee.agents_runtime_pydantic import toolsets as toolsets_module
from angee.agents_runtime_pydantic.runner import _BINARY_CONTENT_OMITTED, _without_binary_content
from angee.agents_runtime_pydantic.toolsets import (
    MAX_TOOL_RESULT_CHARS,
    TOOL_RESULT_TRUNCATED,
    AngeeToolset,
    ToolGrantAccess,
    _assert_in_process_compatible,
)
from angee.base.mixins import AuditMixin
from tests.conftest import _clear_model_tables
from tests.conftest import _create_missing_tables as _create_tables
from tests.test_agents_graphql import (
    Agent,
    AgentSession,
    MCPServer,
    MCPTool,
    User,
)
from tests.test_agents_graphql import (
    agents_console_tables as agents_console_tables,
)


class AgentToolWriteProbe(AuditMixin, models.Model):
    """A neutral audit row proving which user an in-process write attributes."""

    label = models.CharField(max_length=100)

    class Meta:
        app_label = "agents"
        db_table = "test_agents_tool_write_probe"


@pytest.fixture()
def agent_tooling_tables(agents_console_tables: None) -> Any:
    """Add the audit probe table to the concrete agents/REBAC fixture."""

    del agents_console_tables
    created = _create_tables((AgentToolWriteProbe,))
    try:
        yield
    finally:
        _clear_model_tables((AgentToolWriteProbe,))
        if created:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(AgentToolWriteProbe)


def _registered_server(*functions: tuple[Any, bool]) -> FastMCP:
    """Return a FastMCP registry whose functions declare read/write posture."""

    server = FastMCP(name="agent-tooling-test")
    for function, read_only in functions:
        server.add_tool(
            Tool.from_function(
                function,
                annotations=ToolAnnotations(readOnlyHint=read_only),
            )
        )
    return server


def _native_context() -> SimpleNamespace:
    """Return the one RunContext field native advertisement consumes."""

    return SimpleNamespace(max_retries=2)


def test_grant_advertisement_shares_one_accessible_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent native/external advertisement consumes one cached ``accessible`` call."""

    calls: list[SubjectRef] = []

    def accessible(agent: SubjectRef) -> frozenset[str]:
        calls.append(agent)
        return frozenset({"read_sessions"})

    monkeypatch.setattr(toolsets_module, "_accessible_tool_names", accessible)
    agent = SubjectRef.of("agents/agent", "one")
    access = ToolGrantAccess(agent)

    async def read_twice() -> tuple[frozenset[str], frozenset[str]]:
        first, second = await asyncio.gather(access.granted_names(), access.granted_names())
        return first, second

    assert async_to_sync(read_twice)() == (frozenset({"read_sessions"}),) * 2
    assert calls == [agent]


@pytest.mark.django_db(transaction=True)
def test_native_tool_grants_split_read_and_write_actors_and_regate(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2M grants advertise live tools; reads impersonate owner and writes attribute agent."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="native-tool-owner")
    other = User.objects.create_user(username="native-tool-other")
    with system_context(reason="test native tool setup"):
        agent = Agent.objects.create(name="Native Tool Agent", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner, title="current")
        owner_extra = AgentSession.objects.create(agent=agent, owner=owner, title="owner extra")
        AgentSession.objects.create(agent=agent, owner=other, title="other")
        server_row = MCPServer.objects.create(name="native", config={"builtin": "angee"})

    async def read_sessions() -> list[str]:
        def visible() -> list[str]:
            return [str(value) for value in AgentSession.objects.order_by("sqid").values_list("sqid", flat=True)]

        return await sync_to_async(visible, thread_sensitive=True)()

    async def write_probe(label: str) -> dict[str, int | None]:
        probe = await sync_to_async(AgentToolWriteProbe.objects.create, thread_sensitive=True)(label=label)
        return {"created_by_id": probe.created_by_id}

    registry = _registered_server((read_sessions, True), (write_probe, False))
    monkeypatch.setattr(toolsets_module, "mcp_server", lambda: registry)

    with system_context(reason="test native tool catalogue"):
        read_row = MCPTool.objects.create(server=server_row, name="read_sessions")
        write_row = MCPTool.objects.create(server=server_row, name="write_probe")

    native = AngeeToolset(session, ToolGrantAccess(agent.principal_subject()))
    assert async_to_sync(native.get_tools)(_native_context()) == {}

    with actor_context(agent.principal_subject()):
        assert list(AgentSession.objects.values_list("sqid", flat=True)) == []

    with system_context(reason="test native tool grants"):
        agent.mcp_tools.add(read_row, write_row)

    native = AngeeToolset(session, ToolGrantAccess(agent.principal_subject()))
    advertised = async_to_sync(native.get_tools)(_native_context())
    assert set(advertised) == {"read_sessions", "write_probe"}
    assert all(tool.tool_def.defer_loading for tool in advertised.values())

    owner_rows = async_to_sync(native.call_tool)(
        "read_sessions",
        {},
        _native_context(),
        advertised["read_sessions"],
    )
    assert set(owner_rows) == {str(session.sqid), str(owner_extra.sqid)}

    with override_settings(ANGEE_ACTOR_USER_RESOLVERS={"agents/agent": "angee.agents.actor_resolvers.agent_user_id"}):
        written = async_to_sync(native.call_tool)(
            "write_probe",
            {"label": "agent-authored"},
            _native_context(),
            advertised["write_probe"],
        )
    assert written == {"created_by_id": agent.user_id}
    assert written["created_by_id"] != owner.pk

    with system_context(reason="test native tool revocation"):
        agent.mcp_tools.remove(read_row)
    with pytest.raises(ModelRetry, match="no longer have permission"):
        async_to_sync(native.call_tool)(
            "read_sessions",
            {},
            _native_context(),
            advertised["read_sessions"],
        )


@pytest.mark.django_db(transaction=True)
def test_toolrole_and_group_grantee_paths(agent_tooling_tables: None) -> None:
    """The approved userset arms resolve toolrole hierarchy and auth-group membership."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="tool-bundle-owner")
    with system_context(reason="test tool bundle setup"):
        agent = Agent.objects.create(name="Tool Bundle Agent", owner=owner)
        write_relationships(
            [
                RelationshipTuple(
                    resource=ObjectRef("agents/toolrole", "readers"),
                    relation="member",
                    subject=agent.principal_subject(),
                ),
                RelationshipTuple(
                    resource=tool_grant_ref("role_reader"),
                    relation="grantee",
                    subject=SubjectRef.of("agents/toolrole", "readers", "effective_member"),
                ),
                RelationshipTuple(
                    resource=ObjectRef("auth/group", "research"),
                    relation="member",
                    subject=to_subject_ref(owner),
                ),
                RelationshipTuple(
                    resource=tool_grant_ref("group_reader"),
                    relation="grantee",
                    subject=SubjectRef.of("auth/group", "research", "member"),
                ),
            ]
        )

    assert (
        backend()
        .check_access(
            subject=agent.principal_subject(),
            action="use",
            resource=tool_grant_ref("role_reader"),
        )
        .allowed
    )
    assert (
        backend()
        .check_access(
            subject=to_subject_ref(owner),
            action="use",
            resource=tool_grant_ref("group_reader"),
        )
        .allowed
    )


def test_tool_grant_anchors_are_tableless() -> None:
    """The zed type anchors remain abstract/managed-false and cannot emit tables."""

    assert ToolGrant._meta.abstract and not ToolGrant._meta.managed
    assert ToolRole._meta.abstract and not ToolRole._meta.managed


@pytest.mark.django_db(transaction=True)
def test_native_tool_error_mapping_ceiling_and_context_constraint(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct Tool.run calls retry safely, bound output, and reject request-only context."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="native-errors-owner")
    with system_context(reason="test native errors setup"):
        agent = Agent.objects.create(name="Native Errors Agent", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        server_row = MCPServer.objects.create(name="native-errors", config={"builtin": "angee"})

    def huge_result() -> str:
        return "x" * (MAX_TOOL_RESULT_CHARS + 100)

    def explode_secret() -> str:
        raise RuntimeError("database password is secret")

    def typed_echo(value: int) -> int:
        return value

    registry = _registered_server((huge_result, True), (explode_secret, True), (typed_echo, True))
    monkeypatch.setattr(toolsets_module, "mcp_server", lambda: registry)
    with system_context(reason="test native errors grant"):
        rows = [
            MCPTool.objects.create(server=server_row, name=name)
            for name in ("huge_result", "explode_secret", "typed_echo")
        ]
        agent.mcp_tools.add(*rows)

    native = AngeeToolset(session, ToolGrantAccess(agent.principal_subject()))
    advertised = async_to_sync(native.get_tools)(_native_context())
    bounded = async_to_sync(native.call_tool)("huge_result", {}, _native_context(), advertised["huge_result"])
    assert isinstance(bounded, str)
    assert len(bounded) == MAX_TOOL_RESULT_CHARS
    assert bounded.endswith(TOOL_RESULT_TRUNCATED)

    with pytest.raises(ModelRetry, match="could not be completed") as secret_error:
        async_to_sync(native.call_tool)("explode_secret", {}, _native_context(), advertised["explode_secret"])
    assert "password" not in str(secret_error.value)

    with pytest.raises(ModelRetry, match="Invalid tool arguments"):
        async_to_sync(native.call_tool)(
            "typed_echo", {"value": "not-an-int"}, _native_context(), advertised["typed_echo"]
        )

    def request_bound(context: Context) -> str:
        return str(context)

    request_tool = Tool.from_function(
        request_bound,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    with pytest.raises(Exception, match="request-scoped FastMCP Context"):
        _assert_in_process_compatible(request_tool)


def test_tool_search_activation_replays_from_persisted_history() -> None:
    """Deferred tools remain hidden until search and reappear after history round-trip."""

    async def query_sessions() -> str:
        """Query persisted agent sessions."""

        return "sessions"

    wrapped = ToolSearch().get_wrapper_toolset(FunctionToolset([query_sessions], defer_loading=True))
    ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage(), max_retries=1)
    initial = async_to_sync(wrapped.get_tools)(ctx)
    assert initial["query_sessions"].tool_def.defer_loading is True
    assert "search_tools" in initial

    found = async_to_sync(wrapped.call_tool)(
        "search_tools",
        {"queries": ["sessions"]},
        ctx,
        initial["search_tools"],
    )
    history = [
        ModelRequest(
            parts=[
                ToolSearchReturnPart(
                    content=found,
                    tool_call_id="search-call",
                )
            ]
        )
    ]
    persisted = to_jsonable_python(history)
    replayed = ModelMessagesTypeAdapter.validate_python(persisted)
    next_ctx = dataclasses.replace(ctx, discovered_tool_names=parse_discovered_tools(replayed))
    activated = async_to_sync(wrapped.get_tools)(next_ctx)

    assert next_ctx.discovered_tool_names == {"query_sessions"}
    assert activated["query_sessions"].tool_def.defer_loading is False


def test_binary_tool_content_is_removed_before_replay_persistence() -> None:
    """Raw tool bytes never survive the runner's replay-state projection."""

    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="binary_probe",
                    content=BinaryContent(b"raw-secret-bytes", media_type="application/octet-stream"),
                    tool_call_id="binary-call",
                )
            ]
        )
    ]

    persisted = to_jsonable_python(_without_binary_content(messages))
    assert _BINARY_CONTENT_OMITTED in str(persisted)
    assert "raw-secret-bytes" not in str(persisted)
    ModelMessagesTypeAdapter.validate_python(persisted)
