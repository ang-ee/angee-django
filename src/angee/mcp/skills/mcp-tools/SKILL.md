---
name: mcp-tools
description: "Authors an Angee addon's MCP tools: mcp_tools.py with register(server), GraphQLTool specs compiled from a schema bucket, actor scope and permission delegation, mount and lifespan facts, tool tests. Use for 'add an MCP tool', 'expose a model to agents', 'mcp_tools.py'."
---

# MCP Tools

Run `angee-design` first. This skill declares tools only; the reference example throughout is
`examples/src/example/notes/mcp_tools.py`. A GraphQL-backed tool runs the same actor-scoped operation a
browser would, so the operation it names must already exist (`angee-graphql`); a hand-written tool reads
through the same ambient actor.

## Use when

- "Expose notes / pages / <model> to agents over MCP."
- "Add `list_<x>` / `read_<x>` / `create_<x>` tools to `<addon>`."
- "Write `mcp_tools.py` with its conventional `register` contribution."
- Not for the bearer→actor verifier, tool grants or the agent catalogue: those belong to `angee.agents`
  ([mcp_verifier](../../../agents/mcp_verifier.py), [grants](../../../agents/grants.py)).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/mcp_tools.py` | `register(server: FastMCP) -> None`: which operations the addon exposes and how they project | `register` in `examples/src/example/notes/mcp_tools.py` |
| `angee.mcp.server` | The one process-wide `FastMCP`, registrar discovery in install order, `MOUNT_PATH` | `mcp_server`, `tool_registrar`, `MOUNT_PATH` in `src/angee/mcp/server.py` |
| `angee.mcp.graphql` | `GraphQLTool` declaration, compiler, limits, actor-scoped execution | `GraphQLTool`, `register_graphql_tools`, `DEFAULT_QUERY_LIMIT`, `MAX_QUERY_LIMIT`, `execute_under_actor`, `_compile`, `_validate` in `src/angee/mcp/graphql.py` |
| `angee.mcp.middleware`, `angee.mcp.verifier` | Actor bracketing per call; bearer→`SubjectRef` through `ANGEE_MCP_ACTOR_VERIFIER` | `ActorMiddleware` in `src/angee/mcp/middleware.py`, `MCPActorVerifier` and `RebacTokenVerifier` in `src/angee/mcp/verifier.py` |
| `angee.mcp.asgi` | `http_mounts()` and fresh `lifespans()` factories read by `angee.server.asgi`, entered per serving process | `src/angee/mcp/asgi.py`, `angee.server.asgi` |
| `angee.mcp.resource_tools`, `angee.mcp.apps` | Generated console readers, `RESOURCE_READER_TOOL_TAG`, check `angee.mcp.E001` | `register_resource_tools`, `check_resource_tool_specs` in `src/angee/mcp/resource_tools.py`, `MCPConfig` in `src/angee/mcp/apps.py` |
| `angee.base.addons` | Optional addon-module discovery; MCP owns registrar validation | [Base adapter](../../../base/addons.py), [MCP server](../../server.py) |
| `tests/test_<addon>_mcp.py`, `<consumer>/tests/test_mcp.py` | Compile-and-register tests; StreamableHTTP round trip | `_registered_tools`, `test_appconfig_wires_the_registrar` in `tests/test_knowledge_mcp.py`; `MCPStreamableHTTPMixin`, `NotesMCPServerTests` in `examples/src/example/notes/tests/test_mcp.py` |

## Reuse first

- `GraphQLTool` + `register_graphql_tools` (`src/angee/mcp/graphql.py`): declare an operation and a
  projection; the compiler derives input schema, document, output schema and `readOnlyHint` from the schema bucket.
- The schema bucket's own gates: strawberry `permission_classes`, `RebacManager` scoping, the write backend and
  `action_guard` run inside `execute_under_actor` (`src/angee/mcp/graphql.py`); a tool adds none.
- `ActorMiddleware` enters `rebac.actor_context` per call (`src/angee/mcp/middleware.py`); tool bodies read
  `rebac.current_actor()` ambiently and never take an actor argument.
- Generated readers already cover every console resource (`register_resource_tools` in `src/angee/mcp/resource_tools.py`); curate a tool
  only for a projection, write or search the generated pair cannot express.
- Hand-written FastMCP tools (`server.add_tool(Tool.from_function(...))`) are reserved for data no GraphQL
  operation owns (the catalogue reader `list_resources`, `src/angee/mcp/resource_tools.py`); they read
  through model verbs and the actor-scoped querysets `ActorMiddleware` pins, set `ToolAnnotations(readOnlyHint=...)`
  and avoid request-scoped FastMCP context (`ToolRegistrar` in `src/angee/mcp/server.py`, asserted by
  `_assert_in_process_compatible` in `src/angee/agents_runtime_pydantic/toolsets.py`).

## Steps

1. **Depend on the server addon.** Add `"angee-mcp>=3,<4"` to `[project].dependencies` in `pyproject.toml` (notes:
   `examples/src/example/notes/pyproject.toml`). Do not add a `[tool.angee.mcp]` table: the conventional
   `mcp_tools.py` supplies the conventional `register` callable
   (`src/angee/mcp/server.py`). **Owner rule:** the manifest declares, `angee.mcp.server` resolves
   ([composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).

2. **Pick the operations.** Each tool names one root field of one schema bucket (`schema="public"` default;
   generated readers use `console`). List queries must declare `limit_arg`; the compiler applies
   `DEFAULT_QUERY_LIMIT`/`MAX_QUERY_LIMIT` (`_validate` in `src/angee/mcp/graphql.py`). If the root takes one
   input object → `flatten`; a scalar id → `id_arg` (exposed as `sqid`); bare named arguments → `args`;
   constants the agent never sees → `fixed`. **Owner rule:** the schema owns arguments and gates; the tool
   only projects ([GraphQL and authorization](../../../../../docs/backend/guidelines.md#graphql-and-authorization)).

3. **Write `mcp_tools.py`** (notes, abbreviated; one register call per addon):

   ```python
   from fastmcp import FastMCP
   from angee.mcp.graphql import GraphQLTool, register_graphql_tools

   _SUMMARY = ("sqid", "title", "status", "word_count")   # ungated fields only
   _DETAIL = (*_SUMMARY, "body", "tags")

   def register(server: FastMCP) -> None:
       register_graphql_tools(server, [
           GraphQLTool(operation="notes", name="list_notes", fields=_SUMMARY, limit_arg="limit",
                       description="List the caller's notes, most-recently-updated first."),
           GraphQLTool(operation="notes_by_pk", name="read_note", fields=_DETAIL, id_arg="id",
                       description="Return one note in full by its public id (sqid)."),
           GraphQLTool(operation="insert_notes_one", name="create_note", fields=_DETAIL, flatten="object",
                       description="Create a note owned by the user caller and return it."),
           GraphQLTool(operation="delete_note", name="delete_note", fields=("total_deleted_count",),
                       id_arg="id", fixed={"confirm": True},
                       description="Delete a note the caller may delete, by its public id (sqid)."),
       ])
   ```

   Fields are snake_case leaves or `(name, (child, ...))` branches at most two deep
   (`GraphQLTool` in `src/angee/mcp/graphql.py`; nested shape: `_BACKLINKS` in `src/angee/knowledge/mcp_tools.py`). Project
   only fields every permitted caller may read: a field gated `read__<field> = owner` is redacted to null for a
   non-owner and a non-nullable projection then fails (`examples/src/example/notes/mcp_tools.py`).
   **Owner rule:** `mcp_tools.py` owns registration and nothing else
   ([Django-native rule](../../../../../docs/backend/guidelines.md#django-native-rule)); a bad spec fails at
   server build with the operation and name (`_validate` in `src/angee/mcp/graphql.py`).

4. **Leave actor scope and permissions to the owners.** Tools run as the authenticated actor: the verifier
   named by `ANGEE_MCP_ACTOR_VERIFIER` resolves the bearer (`SETTINGS` in `src/angee/agents/autoconfig.py` →
   `resolve_actor` in `src/angee/agents/mcp_verifier.py`), `ActorMiddleware` brackets the call, and the operation's own zed
   and querysets decide. A write lands with the actor's audit stamps (agent service user:
   `test_agent_bearer_write_stamps_service_user` in `examples/src/example/notes/tests/test_mcp.py`). If a tool needs a rule the schema lacks → add it
   to the model or its verb (`angee-models`, `angee-permissions`), never to the tool. **Owner rule:**
   [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
   own authorization; a tool re-checking it is a second gate.

5. **Mount and lifespan are inherited.** `angee.mcp.asgi.http_mounts()` mounts the StreamableHTTP app at
   `/mcp` and `lifespans()` contributes FastMCP's `app.lifespan` only when any addon registers tools
   (`src/angee/mcp/asgi.py`); `angee.server.asgi` reads both from the addon `asgi` module
   and enters the lifespans per serving process.
   An addon contributes no `asgi.py` for MCP. **Owner rule:** [Serving](../../../../../docs/composer.md#serving)
   owns mounts; the stack row owns the serve mode ([stack](../../../../../docs/stack.md#backend), the channels and FastMCP rows).

6. **Test the tools.** Compile-and-register without HTTP: build `FastMCP(name=...)`, call `register`, and read
   `asyncio.run(server.list_tools())` for `.document`, `.parameters`, `.annotations`
   (`_registered_tools` in `tests/test_knowledge_mcp.py`); assert `tool_registrar(apps.get_app_config("<label>")) is register`
   (`test_appconfig_wires_the_registrar` in `tests/test_knowledge_mcp.py`). For the actor contract, drive the mounted app with a bearer under
   `override_settings(ANGEE_MCP_ACTOR_VERIFIER=f"{__name__}._verify_bearer")` and clear `mcp_server`/`mcp_app`
   caches in `setUp` (`NotesMCPServerTests` in `examples/src/example/notes/tests/test_mcp.py`). Consumer tests live in the
   addon's `tests/`; framework tests in `tests/test_<addon>_mcp.py` (`angee-tests`).

## Do not

- A tool body that filters rows by user or re-checks a permission: a GraphQL-backed tool delegates both to the
  schema under `execute_under_actor` (`src/angee/mcp/graphql.py`); a hand-written tool delegates to the
  model verbs and the actor-scoped querysets `ActorMiddleware` pins (`src/angee/mcp/middleware.py`;
  [REBAC](../../../../../docs/backend/guidelines.md#rebac)); neither adds a second gate
  ([Keep policy above detail](../../../../../docs/guidelines.md#keep-policy-above-detail)).
- An actor, user id or tenant argument on a tool: `ActorMiddleware` supplies the actor ambiently (`src/angee/mcp/middleware.py`; [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)).
- A second FastMCP instance, mount or lifespan in an addon: `angee.mcp.server.mcp_server` is the process-wide owner (`src/angee/mcp/server.py`; [Serving](../../../../../docs/composer.md#serving)).
- A tool registry, decorator or settings list beside `mcp_tools.register`: [MCP server](../../server.py) discovers and validates registrars through Base's optional-module reader ([composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).
- Hand-rolled CRUD readers for a console resource: generated readers already exist and the agents catalogue grants them (`register_resource_tools` in `src/angee/mcp/resource_tools.py`, `sync_resource_reader_grants` in `src/angee/agents/grants.py`; [DRY](../../../../../docs/guidelines.md#dont-repeat-yourself-dry)).
- A list query without `limit_arg` or a page bound above `MAX_QUERY_LIMIT`: the compiler refuses the first and clamps the second (`_validate` in `src/angee/mcp/graphql.py`).
- Projecting owner-gated or nullable-sensitive fields in a tool every actor may call: field gates redact, the projection then fails (`examples/src/example/notes/mcp_tools.py`; [field-backed conditional access](../../../../../docs/backend/guidelines.md#rebac)).
- A base-level `ANGEE_MCP_ACTOR_VERIFIER` default in an addon autoconfig: the catalogue owner supplies it and an empty default clobbers it (`SETTINGS` in `src/angee/mcp/autoconfig.py`; [Autoconfig](../../../../../docs/composer.md#autoconfig)).
- `get_access_token()` or FastMCP `Context` inside a tool callable: a granted tool also runs in-process through the pydantic runtime, outside any FastMCP request, which asserts both (`ToolRegistrar` in `src/angee/mcp/server.py`, `_assert_in_process_compatible` in `src/angee/agents_runtime_pydantic/toolsets.py`).

## Verify

Done means: `register` compiles every spec (the knowledge-style compile test passes), the registrar test proves
the conventional module wires it, `tools/list` over the mounted app advertises the names, and a scoped call
returns only rows the bearer's actor may read. Django's check `angee.mcp.E001` guards generated readers
against console-schema drift (`check_resource_tool_specs` in `src/angee/mcp/resource_tools.py`).

- In a lane (jj workspace: no host, database or JS install): focused pytest on the addon's MCP test, ruff and
  mypy on touched files, `tests/test_layering.py` — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: the composed host check (`manage.py check` runs `angee.mcp.E001`), SDL and codegen
  when the schema changed, and a stack-level call against `/mcp` — [composition and schema](../../../../../docs/checks.md#composition-and-schema).
- Loop: `.agents/skills/slice/SKILL.md`. False green: a passing compile test says nothing about actor scope;
  only the round trip with a bearer does ([what CI actually runs](../../../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- A cached `mcp_server()` outlives `override_settings`; clear `mcp_server` and `mcp_app` per test (`src/angee/mcp/server.py`).
- Agent bearers authenticate as the agent's service user, not its owner; owner-gated fields are null for them (`resolve_actor` in `src/angee/agents/mcp_verifier.py`).
- The MCP path has no request lifecycle; `_execute_sync` recycles connections itself, so do not add signals or middleware for it (`src/angee/mcp/graphql.py`).
- `readOnlyHint` is derived from the root `op_type`; a hand-written tool must declare it or the pydantic runtime refuses to advertise it (`_assert_in_process_compatible` in `src/angee/agents_runtime_pydantic/toolsets.py`).
- Non-GraphQL tools register with `tags`; the resource-reader tag is reserved for generated readers and drives grant sync (`RESOURCE_READER_TOOL_TAG` in `src/angee/mcp/resource_tools.py`, `sync_resource_reader_grants` in `src/angee/agents/grants.py`).

## Read next

- [Django-native rule](../../../../../docs/backend/guidelines.md#django-native-rule), [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts), [Serving](../../../../../docs/composer.md#serving)
- [Stack: backend](../../../../../docs/stack.md#backend) rows FastMCP + mcp and channels/uvicorn
- Skills: `angee-graphql` (the operation), `angee-permissions` (its gates), `angee-tests`, `slice`
