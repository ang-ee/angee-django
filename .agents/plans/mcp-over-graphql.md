# Plan: expose curated GraphQL operations as MCP tools, on FastMCP v2

Goal: stop hand-rolling MCP tool bodies (the notes `mcp_tools.py` re-derives CRUD,
projection, and gating that GraphQL already owns). Instead, register MCP tools that
**execute a named GraphQL operation under the agent's REBAC actor**, deriving the
input/output schema from the schema, and authored instructions as an overlay.

Two threads: **(A)** replatform the `angee.mcp` addon from the official SDK's
bundled FastMCP onto **jlowin FastMCP v2**, and **(B)** build the GraphQL→MCP tool
layer on top of it. (A) is a prerequisite for (B)'s clean shape.

## Why this exists (the duplication)

`examples/notes-angee/addons/example/notes/mcp_tools.py` restates by hand four
things the GraphQL layer already owns, once per model:

| Hand-rolled | Owned by GraphQL |
|---|---|
| `_summary`/`_detail` projections | `NoteType` field set (`notes/schema.py`), introspectable |
| kwarg lists (`title/body/status/…`) | `NoteInput`/`NotePatch` input types |
| `afrom_public_id`/`acreate`/`asave` | `crud(NoteType, …)` resolvers (`angee/graphql/crud.py`) |
| `@rebac_mcp_tool(resource_type, action)` gate | the **public** schema's own actor-scoping + create/write gates |

A custom `@strawberry.mutation` (`provisionAgent`, `createVault`, …) is the **same
shape** as a `crud()` mutation — a named field on a schema root with args, a return
type, a docstring, a gate. So the unit of exposure is **a named GraphQL operation**,
and CRUD is just the factory-generated case.

## The authz clincher (forces execute-the-operation)

A custom mutation's authorization lives in `@strawberry.mutation(permission_classes=…)`
— a field extension that **only fires during schema execution**. Re-calling a
resolver directly would bypass the gate. So the only correct *and* DRY way to expose
operations is to **execute them** under the actor and let strawberry run its own
permission_classes + REBAC scoping. This is uniform across CRUD and custom ops.

Target the **public, actor-scoped** schema bucket (`notes/schema.py` `"public"`,
which carries `crud(NoteType,…)` with *no* `permission_classes` — pure `RebacManager`
scoping), **not** the `"console"` bucket (admin-elevated, would over-grant an agent).
`tests/conftest.py` already proves off-request execution under an actor:
`with actor_context(actor): schema.execute_sync(query, context_value=SimpleNamespace(request=request))`.

---

## A. Replatform `angee.mcp` onto jlowin FastMCP v2

### Decision & rationale (researched 2026-06-15)

- FastMCP **v1 was donated into the official SDK** — `mcp.server.fastmcp` *is* that
  v1. jlowin **v2 is a superset built on top of the low-level `mcp` SDK** (pins
  `mcp>=1.24,<2`); it wraps the protocol/transport/auth-provider core, doesn't
  reimplement it. Migrating is not a rewrite.
- The official SDK is **renaming `mcp.server.fastmcp.FastMCP` → `McpServer`** in its
  own v2 (beta ~2026-06-30, stable ~2026-07-27). The import the shipped addon stands
  on is sunsetting regardless — "stay to avoid churn" is moot.
- The curation cluster we need is **v2-only**: explicit `Tool` objects with
  hand-supplied input/output JSON schemas; **tool transformation** (`Tool.from_tool`
  / `ArgTransform` — rename/hide/pin/re-document args); server **middleware**
  (`on_call_tool`); `mount`/composition.
- **Caveats:** v2 is maintained by Prefect, fast minor cadence, v3 brewing. Mitigate:
  **pin tightly** (`fastmcp==2.14.x`) and keep it **wrapped behind the `angee.mcp`
  boundary** — no `fastmcp` imports leak past the addon.

### Migration delta (LOW effort — auth contract is identical)

| Concern | SDK FastMCP (shipped) | FastMCP v2 |
|---|---|---|
| Construct | `FastMCP(…, stateless_http=True, json_response=True)` + `AuthSettings(token_verifier=…)` | `FastMCP(name, auth=RebacTokenVerifier(…))`; pass `stateless_http`/`json_response` to `http_app()` |
| ASGI app | `app.streamable_http_app()` | `mcp.http_app(path="/mcp", stateless_http=True, json_response=True)` |
| Mount | `Mount("/mcp", streamable_http_app)` (in `angee.asgi`) | same shape, `Mount("/mcp", app=mcp_app)` |
| Lifespan | host calls `app.session_manager.run()` once | host **enters `mcp_app.lifespan`** (async CM); never touch `session_manager` |
| Verifier base | `mcp.server.auth.provider.TokenVerifier` | `fastmcp.server.auth.TokenVerifier`; `verify_token(self, token) -> AccessToken \| None` **unchanged** |
| Identity carrier | `AccessToken.subject` | **no `subject`** → carry `SubjectRef` in `AccessToken.claims["subject"]` |
| Readback in tool | SDK `get_access_token()` | `from fastmcp.server.dependencies import get_access_token`; read `.claims["subject"]` |
| Per-call actor | per-tool `request_actor()` plumbing | **`on_call_tool` middleware** sets `actor_context` around every call (new, cleaner) |

Concrete edits to shipped files:
1. `addons/angee/mcp/verifier.py` — rebase `RebacTokenVerifier` onto
   `fastmcp.server.auth.TokenVerifier`; return `AccessToken(…, claims={"subject": str(ref)})`.
2. `addons/angee/mcp/server.py` — build with `FastMCP(name, auth=…)`; app via
   `mcp.http_app(path="/mcp", stateless_http=True, json_response=True)`. Drop
   `AuthSettings`/`AuthSettings.issuer_url` (no OAuth routes for a bare verifier).
3. `addons/angee/mcp/asgi.py` + `angee/asgi.py` lifespan arm — enter/exit
   `mcp_app.lifespan(app)` on startup/shutdown instead of `session_manager.run()`.
4. New `on_call_tool` middleware in `angee.mcp` — **the MCP analog of rebac's
   `ActorMiddleware`**: read the FastMCP token once
   (`SubjectRef.parse(get_access_token().claims["subject"])` — the only irreducible
   transport glue, since authn is the transport's job, not rebac's per proposal 0004),
   and bracket the call in rebac's `actor_context(...)`. Tool bodies and the GraphQL
   executor then read the actor via rebac's **`current_actor()`** — the owner. This
   **subsumes `addons/angee/mcp/actors.py` (`actor_from_request` /
   `REBAC_MCP_ACTOR_RESOLVER`) and `tools.py` (`request_actor`)**, which re-derive the
   ambient-actor pattern rebac already owns.
5. Deps: add `fastmcp==2.14.x` to `pyproject.toml`/`uv.lock` (`mcp` stays transitive).
   `docs/stack.md` `mcp` row → jlowin FastMCP v2.

---

## B. The GraphQL→MCP tool layer (`angee.mcp.graphql`)

### One declaration, two kinds of tool, four levers

The `mcp_tools` manifest becomes a **declarative list of specs** (introspectable →
catalogue rows, deterministic, fail-fast), not imperative `register(server)` calls:

```python
# notes/apps.py
mcp_tools = "mcp_tools.TOOLS"   # dotted ref, same shape as `schemas`

# notes/mcp_tools.py
TOOLS = [
    GraphQLTool("notes", name="list_notes",
                fields=["sqid", "title", "status", "word_count"],   # output projection limit
                description="List the caller's notes, newest first. Call before create_note to avoid dupes."),
    GraphQLTool("note", name="read_note",
                description="Fetch one note in full by sqid. Call before update_note to preserve untouched fields."),
    GraphQLTool("createNote", name="create_note", flatten="input",  # lift NoteInput fields to flat args
                args=["title", "body", "tags"], fixed={"status": "draft"},  # arg allow-list + pinned value
                arg_docs={"tags": "Lowercase single-word topic tags."},
                description="Create a draft note owned by the caller. Always set a descriptive title."),
    CustomTool(handler=summarize_notes, name="summarize_notes",     # escape hatch (not a GraphQL op)
               description="Summarize the caller's notes into a digest."),
]
```

Levers — each answers a "beyond the schema" need the GraphQL types can't:
- **`fields`** — output projection whitelist (economy + privacy).
- **`args` / `fixed`** — input allow-list + pinned/injected values (scoping beyond REBAC;
  `fixed` may be `lambda actor: …` to bind to actor-derived scope).
- **`description` / `arg_docs`** — authored agent-facing instructions (the schema gives
  *structure*; you author *intent*). The GraphQL docstring is dev-facing, not enough.
- **`flatten` / proxy** — reshape: lift a nested input object to flat args (agents do
  better flat); optional `before`/`after` hooks (phase 2).

### How it folds into v2 (build Tool objects directly)

Because v2 accepts explicit schemas, the compiler does **not** synthesize Python
functions. Per `GraphQLTool`:

1. Introspect `GraphQLSchemas.from_discovery().build(schema)._schema` (graphql-core
   `GraphQLSchema`) → the root `GraphQLField` for the operation (`.args` each a
   `GraphQLArgument` with `.type`/`.default_value`/`.description`, `.type`, `.description`).
2. **One mapper** `graphql_type → JSON Schema`: scalars, enums, input objects
   (recurse), lists, NonNull→required. Apply `fields`/`args`/`fixed`/`flatten`.
   → `parameters` (input schema). graphql-core is already a (transitive) dep; **no
   codegen dependency** — this is a small runtime mapper, not generated files.
3. Build the output `output_schema` from the projection over the return type
   (scalar leaves; known wrappers `OffsetPaginated`/Connection → `{results[…], totalCount}`;
   list/optional unwrap; **unions deferred**).
4. `Tool(name=snake(op), description=overlay, parameters=…, output_schema=…,
   fn=execute_under_actor, annotations=ToolAnnotations(readOnlyHint=is_query,
   destructiveHint=is_delete))` → `server.add_tool(tool)`.

`CustomTool` is a function (or pre-built `Tool`) → `add_tool`. A custom body that's
*mostly* GraphQL reuses `execute_under_actor(schema, doc, vars)` — escape hatch, not
duplication hatch.

`execute_under_actor`: build/cache the operation document, map args→variables (incl.
sqid→GlobalID), `await schema.execute(doc, variable_values, context_value)` (actor is
already ambient via the `on_call_tool` middleware), raise on `result.errors`, return
the projection (v2 validates against `output_schema`, emits structured content).
`context_value` is a request shim carrying the actor for resolvers that read
`info.context` (e.g. `createVault(info)`).

### Build-time validation (fail-fast)

When the server builds: operation exists in the bucket; `fields`/`args` names exist on
the type/args; `flatten` target is an input object; `fixed` keys are real args; **a
mutation is never exposed unless explicitly listed** (Apollo/Cosmo "Explicit" mode);
no cross-addon MCP name collisions. A bad spec breaks `angee dev` startup, not a live
call.

---

## Resolved decisions

- **FastMCP base:** jlowin **FastMCP v2** (pinned, wrapped behind `angee.mcp`).
- **Agent-facing id:** **sqid**; the mapper translates sqid↔`relay.GlobalID` at the boundary.
- **Ad-hoc mode:** **none** — curated per-operation tools only (no generic `execute`).
- **Mutations:** explicit allow-list only; never auto-exposed.
- **Description precedence:** GraphQL field `.description` (auto) < spec `description`
  (authored) < catalogue `MCPTool.description` (data, phase 2). Mirrors Apollo's tiers.
- **`flatten` default:** on for a single top-level input object (`input`/`data`); opt out
  with `flatten=None`.

## Prior-art grounding (researched)

- **Per-operation tools** = production default (Apollo MCP Server, WunderGraph Cosmo).
- **Overlay over derived descriptions** = Apollo's `overrides.descriptions` precedence.
- **Execute-under-actor is ahead of the field** — no surveyed server runs the operation
  under the calling agent's real identity through the resolver; they re-encode static
  scopes. We enforce authz at the data owner.
- **Avoid:** the generic single `query-graphql` tool in prod (its own author redirects to
  per-operation); auto-derived schema docs as the *primary* description; static header tokens.

## Phasing

1. **A — replatform** to FastMCP v2 (5 edits above) + `on_call_tool` actor middleware.
   Keep the existing notes tools working through it. Proves mount/auth/lifespan on v2.
   **✅ DONE (2026-06-15):** verifier rebased onto `fastmcp.server.auth.TokenVerifier`
   (actor on `subject` + `claims`); `ActorMiddleware.on_call_tool` brackets each call in
   `actor_context`; `server.py` on `fastmcp.FastMCP` + `http_app(path, stateless_http,
   json_response)`; `actors.py`/`tools.py` + the `REBAC_MCP_ACTOR_RESOLVER` setting
   deleted (rebac's default resolver + ambient `current_actor()` cover it); notes tools
   drop `ctx`/`request_actor` and read the ambient actor. `angee/asgi.py` unchanged — its
   `_Lifespan` already drives each mount's `router.lifespan_context`. Notes MCP round-trip
   + 401 green; `test_asgi.py` green; ruff + mypy clean.
2. **B1** — `angee.mcp.graphql`: `GraphQLTool`/`CustomTool` specs, the graphql-core→JSON
   mapper, `execute_under_actor`, build-time validation. Convert notes (deletes
   hand-rolled `mcp_tools.py`); add `createVault`/`createPage` + one `CustomTool` to
   exercise all three paths and the `flatten`/`fields`/sqid rules.
   **✅ engine + notes conversion DONE (2026-06-15):** `execute_under_actor` (verified
   off-request, scoped), the `GraphQLTool` spec + `_compile`/`register_graphql_tools`
   seam, the graphql-core→JSON-Schema mapper, projection (camelCase↔snake_case,
   `relay.GlobalID`↔`sqid` via base64), operation-document generation, and
   `ToolAnnotations` from op kind. Notes' four tools are now GraphQL-backed — the
   hand-rolled ORM access/`_summary`/`_detail` are gone; `test_mcp.py` green; ruff + mypy
   clean. **Deferred to B1.x:** the `fixed`/`args` levers, `CustomTool`, the
   `createVault`/`createPage` + `CustomTool` example, explicit build-time validation
   (unknown op / bad field name / name collisions), and union/interface returns.
3. **B2 (later)** — generate `MCPTool` catalogue rows from specs; `Agent.mcp_tools` M2M
   as the per-agent allow-list (closes the `mcp_verifier.py` TODO "which tools the agent
   may use"); operator-agent server (admin actor) for console actions; unions.

## Risks / verify

- **Prefect coupling / v2 cadence** → pin `fastmcp==2.14.x`, wrap behind the addon.
- **Lifespan ownership** (drive `mcp_app.lifespan`, never `session_manager.run()`) — one
  integration test; forgetting yields "task group not initialized".
- **`AccessToken.subject` gone** → `claims["subject"]`; update verifier + middleware.
- **sqid↔GlobalID** — settle once in the mapper; load-bearing for every id-taking op.
- **Context-dependent resolvers** (read `info.context` beyond the actor) — flag at
  registration; provide a faithful request shim or don't expose.
- **Union/interface returns** — defer (raise "not MCP-exposable yet") rather than emit a
  silently-wrong projection.
- **Dependency weight (follow-up)** — the top-level ``fastmcp`` pulled a heavy transitive
  set (``redis``, ``pydocket``, ``opentelemetry``, …). Trim to ``fastmcp-slim`` + only the
  needed extras to shrink the runtime install. (The ``authlib.jose`` deprecation warning at
  import is fastmcp's own, benign.)

## Files

- Edit: `addons/angee/mcp/{server,verifier,asgi}.py`, `angee/asgi.py`,
  `addons/angee/mcp/autoconfig.py`, `pyproject.toml`, `uv.lock`, `docs/stack.md`.
- New: `addons/angee/mcp/middleware.py` (actor `on_call_tool`),
  `addons/angee/mcp/graphql.py` (specs + compiler + mapper + `execute_under_actor`).
- Delete: `addons/angee/mcp/tools.py` **+ `addons/angee/mcp/actors.py` and the
  `REBAC_MCP_ACTOR_RESOLVER` arm in `autoconfig.py`** (subsumed by the `on_call_tool`
  actor middleware + rebac's `current_actor()`); `addons/angee/agents/mcp_verifier.py`
  scan path (folded into the v2 verifier); the hand-rolled
  `examples/.../notes/mcp_tools.py` body.
