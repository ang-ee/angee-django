"""End-to-end contracts for REBAC-gated native agent tools."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import reversion
from asgiref.sync import async_to_sync, sync_to_async
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection, models, transaction
from django.test import override_settings
from fastmcp import Context, FastMCP
from fastmcp.tools import Tool, ToolResult
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
from rebac.models import PermissionAuditEvent
from rebac.relationships import write_relationships
from reversion.models import Version

from angee.agents import grants as grants_module
from angee.agents import provisioning
from angee.agents.grants import (
    RESOURCE_READER_ROLE,
    TOOL_GRANT_RESOURCE_TYPE,
    resync_tool_grants,
    sync_builtin_tool_catalogue,
    tool_grant_ref,
)
from angee.agents.models import ToolGrant, ToolRole
from angee.agents_runtime_pydantic import toolsets as toolsets_module
from angee.agents_runtime_pydantic.runner import _BINARY_CONTENT_OMITTED, _without_binary_content
from angee.agents_runtime_pydantic.toolsets import (
    MAX_TOOL_RESULT_CHARS,
    TOOL_RESULT_TRUNCATED,
    AngeeToolset,
    ToolGrantAccess,
    _accessible_tool_grant_ids,
    _assert_in_process_compatible,
)
from angee.base.mixins import AuditMixin
from angee.compose.permissions import apply_schema_paths, extension_source_map, merged_schema_relpath
from angee.fs import write_atomic
from angee.mcp.graphql import _CompiledTool
from angee.mcp.resource_tools import RESOURCE_READER_TOOL_TAG
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


@reversion.register(fields=("label",))
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


@pytest.fixture()
def agent_group_schema(tmp_path: Path) -> Any:
    """Apply the real build-time extension seam for agent membership in IAM groups."""

    app_configs = list(apps.get_app_configs())
    source_map = extension_source_map(app_configs)
    runtime_dir = tmp_path / "runtime"
    for relpath, source in source_map.items():
        write_atomic(runtime_dir / relpath, source)
    changed = {
        config.name: (config, getattr(config, "rebac_schema", None), hasattr(config, "rebac_schema"))
        for config in app_configs
        if merged_schema_relpath(config.name) in source_map
    }
    apply_schema_paths(app_configs, runtime_dir)
    # Plain sync respects the package manager's no_update guard and will not
    # overwrite the already-synced base definition with the folded one.
    call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)
    try:
        yield
    finally:
        for config, original, existed in changed.values():
            if existed:
                config.rebac_schema = original
            elif hasattr(config, "rebac_schema"):
                delattr(config, "rebac_schema")
        call_command("rebac", "sync", "--force-overwrite", "--yes", verbosity=0)


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
        return frozenset({"mcp_one.read_sessions"})

    monkeypatch.setattr(toolsets_module, "_accessible_tool_grant_ids", accessible)
    agent = SubjectRef.of("agents/agent", "one")
    access = ToolGrantAccess(agent)

    async def read_twice() -> tuple[frozenset[str], frozenset[str]]:
        first, second = await asyncio.gather(access.granted_ids(), access.granted_ids())
        return first, second

    assert async_to_sync(read_twice)() == (frozenset({"mcp_one.read_sessions"}),) * 2
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

    server_sqid = str(server_row.sqid)
    native = AngeeToolset(session, ToolGrantAccess(agent.principal_subject()), server_sqid)
    assert async_to_sync(native.get_tools)(_native_context()) == {}

    with actor_context(agent.principal_subject()):
        assert list(AgentSession.objects.values_list("sqid", flat=True)) == []

    with system_context(reason="test native tool grants"):
        agent.mcp_tools.add(read_row, write_row)

    native = AngeeToolset(session, ToolGrantAccess(agent.principal_subject()), server_sqid)
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
def test_toolrole_and_group_grantee_paths(
    agent_tooling_tables: None,
    agent_group_schema: None,
) -> None:
    """Advertisement resolves toolrole and group arms under the agent principal."""

    del agent_tooling_tables, agent_group_schema
    owner = User.objects.create_user(username="tool-bundle-owner")
    with system_context(reason="test tool bundle setup"):
        agent = Agent.objects.create(name="Tool Bundle Agent", owner=owner)
        server = MCPServer.objects.create(name="bundle-server")
        role_ref = tool_grant_ref(str(server.sqid), "role_reader")
        group_ref = tool_grant_ref(str(server.sqid), "group_reader")
        write_relationships(
            [
                RelationshipTuple(
                    resource=ObjectRef("agents/toolrole", "readers"),
                    relation="member",
                    subject=agent.principal_subject(),
                ),
                RelationshipTuple(
                    resource=role_ref,
                    relation="grantee",
                    subject=SubjectRef.of("agents/toolrole", "readers", "effective_member"),
                ),
                RelationshipTuple(
                    resource=ObjectRef("auth/group", "research"),
                    relation="agent_member",
                    subject=agent.principal_subject(),
                ),
                RelationshipTuple(
                    resource=group_ref,
                    relation="grantee",
                    subject=SubjectRef.of("auth/group", "research", "agent_member"),
                ),
            ]
        )

    advertised = frozenset(
        backend().accessible(
            subject=agent.principal_subject(),
            action="use",
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
        )
    )
    assert advertised == {role_ref.resource_id, group_ref.resource_id}


@pytest.mark.django_db(transaction=True)
def test_server_qualified_grants_do_not_collide(agent_tooling_tables: None) -> None:
    """Same-named tools on two servers grant and revoke independently."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="qualified-grants-owner")
    with system_context(reason="test qualified grants setup"):
        agent = Agent.objects.create(name="Qualified Grants", owner=owner)
        first = MCPServer.objects.create(name="qualified-first")
        second = MCPServer.objects.create(name="qualified-second")
        first_tool = MCPTool.objects.create(server=first, name="search")
        second_tool = MCPTool.objects.create(server=second, name="search")
        agent.mcp_tools.add(first_tool, second_tool)

    first_ref = tool_grant_ref(str(first.sqid), "search")
    second_ref = tool_grant_ref(str(second.sqid), "search")
    assert frozenset(
        backend().accessible(
            subject=agent.principal_subject(),
            action="use",
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
        )
    ) == {first_ref.resource_id, second_ref.resource_id}

    with system_context(reason="test qualified grant revoke"):
        agent.mcp_tools.remove(first_tool)
    assert not backend().check_access(
        subject=agent.principal_subject(), action="use", resource=first_ref
    ).allowed
    assert backend().check_access(
        subject=agent.principal_subject(), action="use", resource=second_ref
    ).allowed


@pytest.mark.django_db(transaction=True)
def test_m2m_grant_write_is_discarded_with_rolled_back_edit(agent_tooling_tables: None) -> None:
    """The on-commit mirror cannot outlive a rolled-back Agent.mcp_tools edit."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="rolled-back-grant-owner")
    with system_context(reason="test rolled back grant setup"):
        agent = Agent.objects.create(name="Rolled Back Grant", owner=owner)
        server = MCPServer.objects.create(name="rolled-back-server")
        tool = MCPTool.objects.create(server=server, name="rolled_back_tool")

    with system_context(reason="test rolled back grant edit"), transaction.atomic():
        agent.mcp_tools.add(tool)
        transaction.set_rollback(True)

    assert not backend().check_access(
        subject=agent.principal_subject(),
        action="use",
        resource=tool_grant_ref(str(server.sqid), tool.name),
    ).allowed


@pytest.mark.django_db(transaction=True)
def test_resync_delete_and_rewrite_are_atomic(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rewrite rolls back the preceding direct-grant deletion."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="atomic-resync-owner")
    with system_context(reason="test atomic resync setup"):
        agent = Agent.objects.create(name="Atomic Resync", owner=owner)
        server = MCPServer.objects.create(name="atomic-resync-server")
        tool = MCPTool.objects.create(server=server, name="atomic_tool")
        agent.mcp_tools.add(tool)
    grant_ref = tool_grant_ref(str(server.sqid), tool.name)
    assert backend().check_access(
        subject=agent.principal_subject(), action="use", resource=grant_ref
    ).allowed

    monkeypatch.setattr(grants_module, "sync_builtin_tool_catalogue", lambda: 0)

    def fail_rewrite(writes: Any) -> None:
        del writes
        raise RuntimeError("rewrite failed")

    monkeypatch.setattr(grants_module, "write_relationships", fail_rewrite)
    with pytest.raises(RuntimeError, match="rewrite failed"):
        resync_tool_grants()
    assert backend().check_access(
        subject=agent.principal_subject(), action="use", resource=grant_ref
    ).allowed


def test_universal_admin_grant_does_not_enumerate_tableless_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The const-admin arm short-circuits before ``accessible`` can enumerate rows."""

    class UniversalBackend:
        def grants_all(self, **kwargs: Any) -> bool:
            assert kwargs["resource_type"] == TOOL_GRANT_RESOURCE_TYPE
            return True

        def accessible(self, **kwargs: Any) -> Any:
            del kwargs
            raise AssertionError("table-less tool grants must not be enumerated")

    monkeypatch.setattr(toolsets_module, "backend", lambda: UniversalBackend())
    assert _accessible_tool_grant_ids(SubjectRef.of("agents/agent", "admin-agent")) is None


@pytest.mark.django_db(transaction=True)
def test_builtin_catalogue_sync_is_deterministic_and_seeds_reader_bundle(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live code projects to MCPTool rows and one sync-owned reader role grant set."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="catalogue-sync-owner")
    with system_context(reason="test builtin catalogue setup"):
        agent = Agent.objects.create(name="Catalogue Sync", owner=owner)
        server = MCPServer.objects.create(name="builtin-catalogue", config={"builtin": "angee"})
        MCPTool.objects.create(server=server, name="stale_tool")

    def query_records(search: str = "") -> dict[str, str]:
        """Query records in the generated-reader bundle."""

        return {"search": search}

    def write_records() -> str:
        """A non-reader builtin tool."""

        return "ok"

    registry = FastMCP(name="catalogue-sync")
    registry.add_tool(
        Tool.from_function(
            query_records,
            annotations=ToolAnnotations(readOnlyHint=True),
            tags={RESOURCE_READER_TOOL_TAG},
        )
    )
    registry.add_tool(
        Tool.from_function(write_records, annotations=ToolAnnotations(readOnlyHint=False))
    )
    monkeypatch.setattr(grants_module, "mcp_server", lambda: registry)

    assert sync_builtin_tool_catalogue() == 2
    assert sync_builtin_tool_catalogue() == 2
    with system_context(reason="test builtin catalogue verify"):
        rows = list(MCPTool.objects.filter(server=server).order_by("name"))
    assert [row.name for row in rows] == ["query_records", "write_records"]
    assert rows[0].description == query_records.__doc__
    assert rows[0].input_schema["properties"]["search"]["type"] == "string"

    with system_context(reason="test builtin reader membership"):
        write_relationships(
            [
                RelationshipTuple(
                    resource=RESOURCE_READER_ROLE,
                    relation="member",
                    subject=agent.principal_subject(),
                )
            ]
        )
    assert frozenset(
        backend().accessible(
            subject=agent.principal_subject(),
            action="use",
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
        )
    ) == {tool_grant_ref(str(server.sqid), "query_records").resource_id}


@pytest.mark.django_db(transaction=True)
def test_production_reader_grant_path_advertises_and_executes_generated_tool(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync + successful in-process provision grants, advertises, and runs a reader."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="generated-reader-owner")
    with system_context(reason="test generated reader setup"):
        agent = Agent.objects.create(
            name="Generated Reader",
            owner=owner,
            runtime_class="pydantic",
        )
        session = AgentSession.objects.create(agent=agent, owner=owner, title="reader session")
        server = MCPServer.objects.create(name="builtin-reader", config={"builtin": "angee"})

    from angee.graphql.schema import GraphQLSchemas
    from angee.mcp import graphql as mcp_graphql
    from angee.mcp.resource_tools import register_resource_tools
    from tests.test_mcp_resource_tools import _FakeSchemas, _resource

    schemas = _FakeSchemas((_resource("probes"),))
    monkeypatch.setattr("angee.mcp.resource_tools.gated_read_fields", lambda model: frozenset())
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    registry = FastMCP(name="production-reader-path")
    register_resource_tools(registry)
    monkeypatch.setattr(grants_module, "mcp_server", lambda: registry)
    monkeypatch.setattr(toolsets_module, "mcp_server", lambda: registry)

    async def execute_probe(
        schema: str,
        document: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert schema == "console" and document.startswith("query ")
        assert variables == {"limit": 25}
        return {
            "probes": [
                {
                    "id": str(session.sqid),
                    "title": "owner-visible",
                    "body": "detail",
                    "optional_secret": None,
                }
            ]
        }

    monkeypatch.setattr(mcp_graphql, "execute_under_actor", execute_probe)

    assert resync_tool_grants() == 0
    assert "query_probes" not in async_to_sync(
        AngeeToolset(
            session,
            ToolGrantAccess(agent.principal_subject()),
            str(server.sqid),
        ).get_tools
    )(_native_context())

    result = provisioning.provision_agent(agent.sqid)
    assert result.ok is True
    native = AngeeToolset(
        session,
        ToolGrantAccess(agent.principal_subject()),
        str(server.sqid),
    )
    advertised = async_to_sync(native.get_tools)(_native_context())
    assert "query_probes" in advertised
    rows = async_to_sync(native.call_tool)(
        "query_probes",
        {},
        _native_context(),
        advertised["query_probes"],
    )
    assert str(session.sqid) in {row["sqid"] for row in rows}


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

    native = AngeeToolset(
        session,
        ToolGrantAccess(agent.principal_subject()),
        str(server_row.sqid),
    )
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


@pytest.mark.django_db(transaction=True)
def test_impersonated_read_post_call_guard_flags_revision_and_rebac_writes(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent evidence survives thread hops and fails misdeclared read calls."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="read-guard-owner")
    with system_context(reason="test read guard setup"):
        agent = Agent.objects.create(name="Read Guard", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        server = MCPServer.objects.create(name="read-guard-server", config={"builtin": "angee"})

    def revision_write() -> str:
        with reversion.create_revision():
            AgentToolWriteProbe.objects.create(label="revision violation")
            reversion.set_user(owner)
        return "should not escape"

    def relationship_write() -> str:
        write_relationships(
            [
                RelationshipTuple(
                    resource=ObjectRef("agents/toolrole", "read-guard-side-effect"),
                    relation="member",
                    subject=agent.principal_subject(),
                )
            ]
        )
        return "should not escape"

    registry = _registered_server((revision_write, True), (relationship_write, True))
    monkeypatch.setattr(toolsets_module, "mcp_server", lambda: registry)
    with system_context(reason="test read guard grants"):
        rows = [
            MCPTool.objects.create(server=server, name=name)
            for name in ("revision_write", "relationship_write")
        ]
        agent.mcp_tools.add(*rows)

    native = AngeeToolset(
        session,
        ToolGrantAccess(agent.principal_subject()),
        str(server.sqid),
    )
    advertised = async_to_sync(native.get_tools)(_native_context())
    with pytest.raises(ModelRetry, match="read-only tool attempted"):
        async_to_sync(native.call_tool)(
            "revision_write",
            {},
            _native_context(),
            advertised["revision_write"],
        )
    assert Version.objects.filter(revision__user=owner).exists()

    with pytest.raises(ModelRetry, match="read-only tool attempted"):
        async_to_sync(native.call_tool)(
            "relationship_write",
            {},
            _native_context(),
            advertised["relationship_write"],
        )
    owner_ref = to_subject_ref(owner)
    assert PermissionAuditEvent.objects.filter(
        actor_subject_type=owner_ref.subject_type,
        actor_subject_id=owner_ref.subject_id,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_graphql_read_hint_cannot_override_structural_mutation_posture(
    agent_tooling_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutation mislabeled read-only is rejected before owner-attributed execution."""

    del agent_tooling_tables
    owner = User.objects.create_user(username="structural-posture-owner")
    with system_context(reason="test structural posture setup"):
        agent = Agent.objects.create(name="Structural Posture", owner=owner)
        session = AgentSession.objects.create(agent=agent, owner=owner)
        server = MCPServer.objects.create(name="structural-posture", config={"builtin": "angee"})
        row = MCPTool.objects.create(server=server, name="lying_graphql_write")
        agent.mcp_tools.add(row)
    attempted: list[bool] = []

    class MisdeclaredGraphQLWrite(_CompiledTool):
        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            del arguments
            attempted.append(True)
            await sync_to_async(AgentToolWriteProbe.objects.create, thread_sensitive=True)(
                label="must never run"
            )
            return ToolResult(structured_content={"result": "wrote"})

    registered = MisdeclaredGraphQLWrite(
        name="lying_graphql_write",
        description="A mutation carrying an untrusted read hint.",
        parameters={"type": "object", "properties": {}},
        annotations=ToolAnnotations(readOnlyHint=True),
        schema_name="console",
        op_type="mutation",
        document="mutation { ignored }",
        payload_field="ignored",
        node_type="Ignored",
        is_list=False,
        leaves=(),
    )
    registry = FastMCP(name="structural-posture")
    registry.add_tool(registered)
    monkeypatch.setattr(toolsets_module, "mcp_server", lambda: registry)

    native = AngeeToolset(
        session,
        ToolGrantAccess(agent.principal_subject()),
        str(server.sqid),
    )
    with pytest.raises(ImproperlyConfigured, match="disagrees with its mutation"):
        async_to_sync(native.get_tools)(_native_context())
    assert attempted == []
    assert not AgentToolWriteProbe.objects.filter(label="must never run").exists()


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
