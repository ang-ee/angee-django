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
| `<addon>/mcp_tools.py` | `register(server: FastMCP) -> None`: which operations the addon exposes and how they project | `examples/src/example/notes/mcp_tools.py:24` |
| `angee.mcp.server` | The one process-wide `FastMCP`, registrar discovery in install order, `MOUNT_PATH` | `src/angee/mcp/server.py:38,49,54,92` |
| `angee.mcp.graphql` | `GraphQLTool` declaration, compiler, limits, actor-scoped execution | `src/angee/mcp/graphql.py:70,73,83,189,226,368,518` |
| `angee.mcp.middleware`, `angee.mcp.verifier` | Actor bracketing per call; bearer→`SubjectRef` through `ANGEE_MCP_ACTOR_VERIFIER` | `src/angee/mcp/middleware.py:20`, `src/angee/mcp/verifier.py:23,27` |
| `angee.mcp.apps.MCPConfig.compose_asgi` | Native `AsgiContribution` mount and lifespan factories, entered per serving process | `src/angee/mcp/apps.py`, `django_compose.asgi` |
| `angee.mcp.resource_tools`, `angee.mcp.apps` | Generated console readers, `RESOURCE_READER_TOOL_TAG`, check `angee.mcp.E001` | `src/angee/mcp/resource_tools.py:35,49,136`, `src/angee/mcp/apps.py:16` |
| `angee.base.addons` | Optional addon-module discovery; MCP owns registrar validation | [Base adapter](../../../base/addons.py), [MCP server](../../server.py) |
| `tests/test_<addon>_mcp.py`, `<consumer>/tests/test_mcp.py` | Compile-and-register tests; StreamableHTTP round trip | `tests/test_knowledge_mcp.py:56,197`, `examples/src/example/notes/tests/test_mcp.py:67,148` |

## Reuse first

- `GraphQLTool` + `register_graphql_tools` (`src/angee/mcp/graphql.py:189,226`): declare an operation and a
  projection; the compiler derives input schema, document, output schema and `readOnlyHint` from the schema bucket.
- The schema bucket's own gates: strawberry `permission_classes`, `RebacManager` scoping, the write backend and
  `action_guard` run inside `execute_under_actor` (`src/angee/mcp/graphql.py:83`); a tool adds none.
- `ActorMiddleware` enters `rebac.actor_context` per call (`src/angee/mcp/middleware.py:20`); tool bodies read
  `rebac.current_actor()` ambiently and never take an actor argument.
- Generated readers already cover every console resource (`src/angee/mcp/resource_tools.py:49`); curate a tool
  only for a projection, write or search the generated pair cannot express.
- Hand-written FastMCP tools (`server.add_tool(Tool.from_function(...))`) are reserved for data no GraphQL
  operation owns (the catalogue reader `list_resources`, `src/angee/mcp/resource_tools.py:53-60`); they read
  through model verbs and the actor-scoped querysets `ActorMiddleware` pins, set `ToolAnnotations(readOnlyHint=...)`
  and avoid request-scoped FastMCP context (`src/angee/mcp/server.py:38-47`, asserted at
  `src/angee/agents_runtime_pydantic/toolsets.py:302`).

## Steps

1. **Depend on the server addon.** Add `"angee-mcp>=3,<4"` to `[project].dependencies` in `pyproject.toml` (notes:
   `examples/src/example/notes/pyproject.toml:15`). Do not add a `[tool.angee.mcp]` table: the conventional
   `mcp_tools.py` supplies the conventional `register` callable
   (`src/angee/mcp/server.py`). **Owner rule:** the manifest declares, `angee.mcp.server` resolves
   ([composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).

2. **Pick the operations.** Each tool names one root field of one schema bucket (`schema="public"` default;
   generated readers use `console`). List queries must declare `limit_arg`; the compiler applies
   `DEFAULT_QUERY_LIMIT`/`MAX_QUERY_LIMIT` (`src/angee/mcp/graphql.py:70-73,518-529`). If the root takes one
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
   (`src/angee/mcp/graphql.py:189-211`; nested shape: `src/angee/knowledge/mcp_tools.py:22-28`). Project
   only fields every permitted caller may read: a field gated `read__<field> = owner` is redacted to null for a
   non-owner and a non-nullable projection then fails (`examples/src/example/notes/mcp_tools.py:17-19`).
   **Owner rule:** `mcp_tools.py` owns registration and nothing else
   ([Django-native rule](../../../../../docs/backend/guidelines.md#django-native-rule)); a bad spec fails at
   server build with the operation and name (`src/angee/mcp/graphql.py:518`).

4. **Leave actor scope and permissions to the owners.** Tools run as the authenticated actor: the verifier
   named by `ANGEE_MCP_ACTOR_VERIFIER` resolves the bearer (`src/angee/agents/autoconfig.py:47` →
   `src/angee/agents/mcp_verifier.py:29`), `ActorMiddleware` brackets the call, and the operation's own zed
   and querysets decide. A write lands with the actor's audit stamps (agent service user:
   `examples/src/example/notes/tests/test_mcp.py:271-284`). If a tool needs a rule the schema lacks → add it
   to the model or its verb (`angee-models`, `angee-permissions`), never to the tool. **Owner rule:**
   [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
   own authorization; a tool re-checking it is a second gate.

5. **Mount and lifespan are inherited.** `angee.mcp.asgi.http_mounts()` mounts the StreamableHTTP app at
   `/mcp` and `lifespans()` contributes FastMCP's `app.lifespan` only when any addon registers tools
   (`src/angee/mcp/asgi.py:19-35`); `MCPConfig.compose_asgi()` returns them through native `AsgiContribution`
   factories; `django_compose.asgi` enters them per serving process.
   An addon contributes no `asgi.py` for MCP. **Owner rule:** [Serving](../../../../../docs/composer.md#serving)
   owns mounts; the stack row owns the serve mode ([stack](../../../../../docs/stack.md#backend), the channels and FastMCP rows).

6. **Test the tools.** Compile-and-register without HTTP: build `FastMCP(name=...)`, call `register`, and read
   `asyncio.run(server.list_tools())` for `.document`, `.parameters`, `.annotations`
   (`tests/test_knowledge_mcp.py:56-69`); assert `tool_registrar(apps.get_app_config("<label>")) is register`
   (`tests/test_knowledge_mcp.py:197-202`). For the actor contract, drive the mounted app with a bearer under
   `override_settings(ANGEE_MCP_ACTOR_VERIFIER=f"{__name__}._verify_bearer")` and clear `mcp_server`/`mcp_app`
   caches in `setUp` (`examples/src/example/notes/tests/test_mcp.py:144-160`). Consumer tests live in the
   addon's `tests/`; framework tests in `tests/test_<addon>_mcp.py` (`angee-tests`).

## Do not

- A tool body that filters rows by user or re-checks a permission: a GraphQL-backed tool delegates both to the
  schema under `execute_under_actor` (`src/angee/mcp/graphql.py:83`); a hand-written tool delegates to the
  model verbs and the actor-scoped querysets `ActorMiddleware` pins (`src/angee/mcp/middleware.py:20`;
  [REBAC](../../../../../docs/backend/guidelines.md#rebac)); neither adds a second gate
  ([Keep policy above detail](../../../../../docs/guidelines.md#keep-policy-above-detail)).
- An actor, user id or tenant argument on a tool: `ActorMiddleware` supplies the actor ambiently (`src/angee/mcp/middleware.py:20`; [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)).
- A second FastMCP instance, mount or lifespan in an addon: `angee.mcp.server.mcp_server` is the process-wide owner (`src/angee/mcp/server.py:54`; [Serving](../../../../../docs/composer.md#serving)).
- A tool registry, decorator or settings list beside `mcp_tools.register`: [MCP server](../../server.py) discovers and validates registrars through Base's optional-module reader ([composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).
- Hand-rolled CRUD readers for a console resource: generated readers already exist and the agents catalogue grants them (`src/angee/mcp/resource_tools.py:49`, `src/angee/agents/grants.py:116`; [DRY](../../../../../docs/guidelines.md#dont-repeat-yourself-dry)).
- A list query without `limit_arg` or a page bound above `MAX_QUERY_LIMIT`: the compiler refuses the first and clamps the second (`src/angee/mcp/graphql.py:73,528`).
- Projecting owner-gated or nullable-sensitive fields in a tool every actor may call: field gates redact, the projection then fails (`examples/src/example/notes/mcp_tools.py:17-19`; [field-backed conditional access](../../../../../docs/backend/guidelines.md#rebac)).
- A base-level `ANGEE_MCP_ACTOR_VERIFIER` default in an addon autoconfig: the catalogue owner supplies it and an empty default clobbers it (`src/angee/mcp/autoconfig.py:5-10`; [Autoconfig](../../../../../docs/composer.md#autoconfig)).
- `get_access_token()` or FastMCP `Context` inside a tool callable: a granted tool also runs in-process through the pydantic runtime, outside any FastMCP request, which asserts both (`src/angee/mcp/server.py:41-46`, `src/angee/agents_runtime_pydantic/toolsets.py:302`).

## Verify

Done means: `register` compiles every spec (the knowledge-style compile test passes), the registrar test proves
the conventional module wires it, `tools/list` over the mounted app advertises the names, and a scoped call
returns only rows the bearer's actor may read. Django's check `angee.mcp.E001` guards generated readers
against console-schema drift (`src/angee/mcp/resource_tools.py:136`).

- In a lane (jj workspace: no host, database or JS install): focused pytest on the addon's MCP test, ruff and
  mypy on touched files, `tests/test_layering.py` — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: the composed host check (`manage.py check` runs `angee.mcp.E001`), SDL and codegen
  when the schema changed, and a stack-level call against `/mcp` — [composition and schema](../../../../../docs/checks.md#composition-and-schema).
- Loop: `.agents/skills/slice/SKILL.md`. False green: a passing compile test says nothing about actor scope;
  only the round trip with a bearer does ([what CI actually runs](../../../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- A cached `mcp_server()` outlives `override_settings`; clear `mcp_server` and `mcp_app` per test (`src/angee/mcp/server.py:54,69`).
- Agent bearers authenticate as the agent's service user, not its owner; owner-gated fields are null for them (`src/angee/agents/mcp_verifier.py:29`).
- The MCP path has no request lifecycle; `_execute_sync` recycles connections itself, so do not add signals or middleware for it (`src/angee/mcp/graphql.py:103`).
- `readOnlyHint` is derived from the root `op_type`; a hand-written tool must declare it or the pydantic runtime refuses to advertise it (`src/angee/agents_runtime_pydantic/toolsets.py:317-324`).
- Non-GraphQL tools register with `tags`; the resource-reader tag is reserved for generated readers and drives grant sync (`src/angee/mcp/resource_tools.py:35`, `src/angee/agents/grants.py:129`).

## Read next

- [Django-native rule](../../../../../docs/backend/guidelines.md#django-native-rule), [GraphQL actor and write contracts](../../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts), [Serving](../../../../../docs/composer.md#serving)
- [Stack: backend](../../../../../docs/stack.md#backend) rows FastMCP + mcp and channels/uvicorn
- Skills: `angee-graphql` (the operation), `angee-permissions` (its gates), `angee-tests`, `slice`
