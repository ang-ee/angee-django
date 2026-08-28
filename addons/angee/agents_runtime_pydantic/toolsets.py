"""Compose REBAC-gated native and remote toolsets for one agent session.

Built-in Angee tools execute directly through their registered FastMCP
``Tool.run`` implementation. FastMCP remains the registration/compiler owner;
the in-process adapter only translates its definitions and results into
pydantic-ai's toolset contract. Remote servers keep pydantic-ai's MCP transport.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Max
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool as FastMCPTool
from fastmcp.tools import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import TextContent
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    FilteredToolset,
    ToolsetTool,
)
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import SchemaValidator, core_schema
from rebac import PermissionDenied, SubjectRef, actor_context, to_subject_ref
from rebac.backends import backend
from rebac.models import PermissionAuditEvent
from reversion.models import Version

from angee.agents.grants import TOOL_GRANT_RESOURCE_TYPE, builtin_mcp_server, tool_grant_ref
from angee.agents.models import BUILTIN_MCP_ANGEE
from angee.mcp.graphql import _CompiledTool
from angee.mcp.server import mcp_server

MAX_TOOL_RESULT_CHARS = 32_000
"""Maximum inline characters returned from one in-process tool call."""

TOOL_RESULT_TRUNCATED = "\n[Tool result truncated. Narrow your query and try again.]"
"""Marker appended when an in-process tool result exceeds the inline ceiling."""

ALWAYS_LOADED_TOOLS = frozenset({"list_resources"})
"""Small native core kept visible beside pydantic-ai's own ``search_tools``."""

_TOOL_ARGS_VALIDATOR = SchemaValidator(
    schema=core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())
)


@dataclass
class ToolGrantAccess:
    """Share one advertisement lookup and fresh per-call checks across toolsets."""

    agent: SubjectRef
    _granted_task: asyncio.Task[frozenset[str] | None] | None = field(default=None, init=False, repr=False)

    async def granted_ids(self) -> frozenset[str] | None:
        """Return accessible qualified ids, or ``None`` for a universal admin grant."""

        if self._granted_task is None:
            self._granted_task = asyncio.create_task(
                sync_to_async(_accessible_tool_grant_ids, thread_sensitive=True)(self.agent)
            )
        return await self._granted_task

    async def check(self, server_sqid: str, tool_name: str) -> None:
        """Re-gate one invocation so revocation beats stale advertisement."""

        allowed = await sync_to_async(_tool_access_allowed, thread_sensitive=True)(
            self.agent,
            server_sqid,
            tool_name,
        )
        if not allowed:
            raise PermissionDenied(f"Agent may not use tool {tool_name!r}.")


@dataclass(kw_only=True)
class _AngeeToolsetTool(ToolsetTool[Any]):
    """pydantic-ai definition paired with its registered FastMCP implementation."""

    registered_tool: FastMCPTool
    writes: bool


@dataclass
class AngeeToolset(AbstractToolset[Any]):
    """Execute granted built-in FastMCP tools natively for one persisted session."""

    session: Any
    access: ToolGrantAccess
    server_sqid: str | None = None

    @property
    def id(self) -> str:
        """Return the stable id of the process-native toolset."""

        return BUILTIN_MCP_ANGEE

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Advertise the one accessible grant set intersected with the live registry."""

        granted = await self.access.granted_ids()
        server_sqid = self.server_sqid
        if server_sqid is None:
            server_sqid = await sync_to_async(lambda: str(builtin_mcp_server().sqid), thread_sensitive=True)()
        server = await sync_to_async(mcp_server, thread_sensitive=True)()
        tools: dict[str, ToolsetTool[Any]] = {}
        for registered in sorted(await server.list_tools(), key=lambda item: item.name):
            name = registered.name
            grant_id = tool_grant_ref(server_sqid, name).resource_id
            if granted is not None and grant_id not in granted:
                continue
            _assert_in_process_compatible(registered)
            annotations = registered.annotations
            assert annotations is not None and annotations.readOnlyHint is not None
            tools[name] = _AngeeToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=registered.description,
                    parameters_json_schema=registered.parameters,
                    return_schema=registered.output_schema,
                    defer_loading=name not in ALWAYS_LOADED_TOOLS,
                    metadata={
                        "annotations": annotations.model_dump(mode="json", by_alias=True, exclude_none=True),
                    },
                ),
                max_retries=ctx.max_retries,
                args_validator=_TOOL_ARGS_VALIDATOR,
                registered_tool=registered,
                writes=_tool_writes(registered),
            )
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: ToolsetTool[Any],
    ) -> Any:
        """Re-gate, select the execution actor, run directly, and bound the result."""

        del ctx
        if not isinstance(tool, _AngeeToolsetTool):
            raise TypeError("AngeeToolset received a tool it did not advertise.")
        try:
            if self.server_sqid is None:
                self.server_sqid = await sync_to_async(
                    lambda: str(builtin_mcp_server().sqid), thread_sensitive=True
                )()
            await self.access.check(self.server_sqid, name)
        except PermissionDenied as error:
            raise ModelRetry("You no longer have permission to use this tool.") from error

        actor = self.access.agent if tool.writes else to_subject_ref(self.session.owner)
        evidence = None
        if not tool.writes:
            evidence = await sync_to_async(_write_evidence_cursor, thread_sensitive=True)(
                actor,
                self.session.owner.pk,
            )
        call_error: ModelRetry | None = None
        result: ToolResult | None = None
        try:
            with actor_context(actor):
                result = await tool.registered_tool.run(tool_args)
        except ValidationError as error:
            call_error = ModelRetry("Invalid tool arguments. Check the tool schema and try again.")
            call_error.__cause__ = error
        except ToolError as error:
            call_error = ModelRetry(str(error))
            call_error.__cause__ = error
        except Exception as error:
            call_error = ModelRetry("The tool could not be completed.")
            call_error.__cause__ = error
        if evidence is not None and await sync_to_async(_has_write_evidence, thread_sensitive=True)(
            evidence,
            actor,
            self.session.owner.pk,
        ):
            raise ModelRetry("A read-only tool attempted to modify persistent state.")
        if call_error is not None:
            raise call_error
        assert result is not None
        return _bounded_tool_result(_tool_result_value(result))


@dataclass
class ToolGrantToolset(WrapperToolset[Any]):
    """Apply the same REBAC grant gate to an external MCP toolset."""

    access: ToolGrantAccess
    server_sqid: str

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Filter remote advertisement through the shared ``use`` grant set."""

        granted = await self.access.granted_ids()
        tools = await self.wrapped.get_tools(ctx)
        if granted is None:
            return tools
        return {
            name: tool
            for name, tool in tools.items()
            if tool_grant_ref(self.server_sqid, name).resource_id in granted
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: ToolsetTool[Any],
    ) -> Any:
        """Re-gate immediately before delegating to the remote MCP transport."""

        try:
            await self.access.check(self.server_sqid, name)
        except PermissionDenied as error:
            raise ModelRetry("You no longer have permission to use this tool.") from error
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)


def toolsets_for_session(session: Any) -> list[Any]:
    """Return native plus remote toolsets authorized for ``session``."""

    agent = session.agent
    selected: dict[Any, dict[str, bool]] = defaultdict(dict)
    for tool in agent.mcp_tools.select_related("server"):
        if tool.enabled:
            selected[tool.server_id][tool.name] = bool(tool.requires_approval)

    access = ToolGrantAccess(agent.principal_subject())
    builtin = builtin_mcp_server()
    native: Any = AngeeToolset(session, access, str(builtin.sqid))
    native_approvals = frozenset(
        name for name, required in selected.get(builtin.pk, {}).items() if required
    )
    if native_approvals:
        native = ApprovalRequiredToolset(
            native,
            approval_required_func=_approval_filter(native_approvals),
        )

    toolsets: list[Any] = [native]
    for server in agent.mcp_servers.select_related("credential").order_by("name"):
        if server.builtin == BUILTIN_MCP_ANGEE:
            continue
        tools = selected.get(server.pk, {})
        if not tools:
            continue
        transport = _transport_for(server)
        toolset: Any = MCPToolset(transport, id=str(server.sqid))
        toolset = FilteredToolset(toolset, filter_func=_tool_filter(frozenset(tools)))
        toolset = ToolGrantToolset(toolset, access, str(server.sqid))
        approvals = frozenset(name for name, required in tools.items() if required)
        if approvals:
            toolset = ApprovalRequiredToolset(
                toolset,
                approval_required_func=_approval_filter(approvals),
            )
        toolsets.append(toolset)
    return toolsets


def _accessible_tool_grant_ids(agent: SubjectRef) -> frozenset[str] | None:
    """Read qualified grant ids without enumerating a table-less universal arm.

    The const-admin permission grants every pure-tuple anchor, which has no Django
    table to enumerate. ``grants_all`` detects that structural case first; ``None``
    is the internal universal sentinel consumed by registry/catalogue intersections.
    """

    access_backend = backend()
    # ``grants_all`` is a LocalBackend capability, not part of the Backend
    # base: the structural universal-arm detection exists precisely because
    # the local backend cannot enumerate a table-less anchor. Backends with
    # native lookup (SpiceDB) enumerate through ``accessible`` directly.
    grants_all = getattr(access_backend, "grants_all", None)
    if callable(grants_all) and grants_all(
        subject=agent,
        action="use",
        resource_type=TOOL_GRANT_RESOURCE_TYPE,
    ):
        return None
    return frozenset(
        access_backend.accessible(
            subject=agent,
            action="use",
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
        )
    )


def _tool_access_allowed(agent: SubjectRef, server_sqid: str, tool_name: str) -> bool:
    """Return the fresh per-call ``use`` decision for one canonical grant ref."""

    return (
        backend()
        .check_access(
            subject=agent,
            action="use",
            resource=tool_grant_ref(server_sqid, tool_name),
        )
        .allowed
    )


def _assert_in_process_compatible(tool: FastMCPTool) -> None:
    """Assert a built-in tool declares actor posture and no request-only context.

    ``Tool.run`` is invoked outside a FastMCP request, so ``Context`` injection and
    ``get_access_token()`` cannot work here. Registration authors must instead use
    the ambient REBAC actor. GraphQL tools derive actor posture from their root
    operation type; other builtins declare it through ``readOnlyHint``.
    """

    _tool_writes(tool)
    if not isinstance(tool, FunctionTool):
        return
    signature = inspect.signature(tool.fn)
    for parameter in signature.parameters.values():
        if (
            getattr(parameter.annotation, "__name__", "") == "Context"
            or type(parameter.default).__name__ == "_CurrentContext"
        ):
            raise ImproperlyConfigured(f"Built-in MCP tool {tool.name!r} depends on request-scoped FastMCP Context.")
    target = inspect.unwrap(tool.fn)
    code = getattr(target, "__code__", None)
    if code is not None and "get_access_token" in code.co_names:
        raise ImproperlyConfigured(f"Built-in MCP tool {tool.name!r} calls request-scoped get_access_token().")


def _tool_writes(tool: FastMCPTool) -> bool:
    """Return actor posture from GraphQL structure or an explicit builtin declaration."""

    annotations = tool.annotations
    if isinstance(tool, _CompiledTool):
        if tool.op_type not in {"query", "mutation"}:
            raise ImproperlyConfigured(
                f"GraphQL MCP tool {tool.name!r} has invalid operation type {tool.op_type!r}."
            )
        structurally_read_only = tool.op_type == "query"
        if annotations is None or annotations.readOnlyHint is not structurally_read_only:
            raise ImproperlyConfigured(
                f"GraphQL MCP tool {tool.name!r} readOnlyHint disagrees with its {tool.op_type} operation."
            )
        return not structurally_read_only
    if annotations is None or annotations.readOnlyHint is None:
        raise ImproperlyConfigured(
            f"Non-GraphQL built-in MCP tool {tool.name!r} must explicitly declare "
            "ToolAnnotations.readOnlyHint."
        )
    return annotations.readOnlyHint is False


@dataclass(frozen=True, slots=True)
class _WriteEvidenceCursor:
    """Persistent audit high-water marks bracketing one impersonated tool call."""

    version_pk: int
    rebac_audit_pk: int


def _write_evidence_cursor(actor: SubjectRef, user_pk: Any) -> _WriteEvidenceCursor:
    """Snapshot owner-attributed revision and REBAC audit rows before a read call."""

    version_pk = Version.objects.filter(revision__user_id=user_pk).aggregate(value=Max("pk"))["value"] or 0
    audit_pk = (
        PermissionAuditEvent.objects.filter(
            actor_subject_type=actor.subject_type,
            actor_subject_id=actor.subject_id,
        ).aggregate(value=Max("pk"))["value"]
        or 0
    )
    return _WriteEvidenceCursor(version_pk=int(version_pk), rebac_audit_pk=int(audit_pk))


def _has_write_evidence(cursor: _WriteEvidenceCursor, actor: SubjectRef, user_pk: Any) -> bool:
    """Return whether the impersonated actor acquired persistent write evidence."""

    return Version.objects.filter(revision__user_id=user_pk, pk__gt=cursor.version_pk).exists() or (
        PermissionAuditEvent.objects.filter(
            actor_subject_type=actor.subject_type,
            actor_subject_id=actor.subject_id,
            pk__gt=cursor.rebac_audit_pk,
        ).exists()
    )


def _tool_result_value(result: ToolResult) -> Any:
    """Map one FastMCP result to pydantic-ai's model-visible return value."""

    if result.is_error:
        raise ToolError("The tool reported an error.")
    text_parts = [part for part in result.content if isinstance(part, TextContent)]
    text_only = len(text_parts) == len(result.content)
    if result.structured_content is not None and text_only:
        structured = result.structured_content
        if len(structured) == 1 and "result" in structured:
            return structured["result"]
        return structured
    if text_only:
        text = "\n".join(part.text for part in text_parts)
        return text
    return [part.model_dump(mode="json", by_alias=True, exclude_none=True) for part in result.content]


def _bounded_tool_result(value: Any) -> Any:
    """Return ``value`` inline when bounded, otherwise a truncated JSON preview."""

    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return value
    preview_length = MAX_TOOL_RESULT_CHARS - len(TOOL_RESULT_TRUNCATED)
    return f"{serialized[:preview_length]}{TOOL_RESULT_TRUNCATED}"


def _transport_for(server: Any) -> Any:
    """Return the owner-native HTTP transport for one external MCP server row."""

    url = str(server.resolved_url or "").strip()
    if not url:
        raise ValueError(f"MCP server {server.name!r} has no addressable URL.")
    headers: dict[str, str] = {}
    if server.credential_id:
        server.credential.ensure_fresh()
        bearer = str(server.credential.secret_value() or "")
        if not bearer:
            raise ValueError(f"MCP server {server.name!r} has an empty credential.")
        headers["Authorization"] = f"Bearer {bearer}"
    return StreamableHttpTransport(url, headers=headers or None)


def _tool_filter(names: frozenset[str]) -> Callable[[Any, Any], bool]:
    """Return a typed pydantic-ai tool-definition filter."""

    def includes(_context: Any, definition: Any) -> bool:
        return bool(definition.name in names)

    return includes


def _approval_filter(names: frozenset[str]) -> Callable[[Any, Any, Any], bool]:
    """Return a typed approval predicate for selected tool names."""

    def requires_approval(_context: Any, definition: Any, _args: Any) -> bool:
        return bool(definition.name in names)

    return requires_approval
