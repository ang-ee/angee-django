# Plan: mount one FastMCP server in the Angee ASGI app; authn in FastMCP, authz in rebac

## Update (shipped): uvicorn lifespan, no lazy-start

The dispatcher below was simplified once we decided to **serve with uvicorn**
instead of Daphne's `runserver`. Daphne sends no ASGI lifespan, which was the
sole reason for the lazy "ensure-started on first `/mcp` request" machinery
(lock + started-flag + dual lifespan/lazy path). uvicorn sends the lifespan, so
`angee.asgi` is back to channels' `ProtocolTypeRouter` with three keys —
`http` (a small prefix router: `/mcp` → mount, else Django), `websocket` (the
channels stack, unchanged), and `lifespan` (a plain handler that enters each
mount's `session_manager.run()` via `AsyncExitStack` at startup, closes at
shutdown). `daphne` was dropped from the deps, `INSTALLED_APPS`, and
`angee.graphql`'s `depends_on`; the dev stack's `django` service now runs
`uvicorn angee.asgi:application --reload`. The MCP server is no static/admin
regression — the composed runtime ships neither. Everything below about the
authn/authz ownership split still holds.

## What changed from v1

Per the steering: **mount the FastMCP ASGI app directly in `angee.asgi.application`
(the fyltr pattern) — no Django-view wrapper, no synthetic scope.** **One
process-wide MCP server** (not per-addon; seam stays count-agnostic so per-server
later is non-breaking). **Maximum DRY: don't hand-roll what an owner already owns
— implement the MCP authorization adapter in `django-zed-rebac` (its proposal
0004), reuse rebac actor primitives + FastMCP auth + channels.** Rename the
fiddly `runtime.py`.

## Ownership map (every concern wired to its owner — the whole point)

| Concern | Owner | Mechanism |
|---|---|---|
| MCP server, JSON-RPC, StreamableHTTP ASGI app, lifespan | **mcp (FastMCP)** | `FastMCP(...).streamable_http_app()` (lifespan = `session_manager.run()`) |
| Bearer → identity (authn) | **Angee `mcp` addon** (transport) | FastMCP `token_verifier` — rebac proposal 0004 says authn is the transport's job, *not* rebac's |
| Credential → `SubjectRef` mapping | **agents addon** (catalogue owner) | supplied via `ANGEE_MCP_ACTOR_VERIFIER`; the verifier uses the `MCPServer.credential` model |
| Actor → permission check + queryset scoping (authz) | **rebac** | `rebac.mcp.rebac_mcp_tool` decorator → `backend().has_access` / `check_new`, `.with_actor` |
| Actor primitives (`SubjectRef`, ambient ContextVar, `bearer_token`, resolver chain, `to_subject_ref`/`grant_subject_ref`, `actor_context`/`asudo`) | **rebac** | imported, never re-derived |
| Mount + lifecycle + scope-type routing | **Angee framework** (`angee/asgi.py`) | one dispatcher built from discovered `asgi.py` contributions |
| ASGI/WebSocket transport | **channels + daphne** | unchanged for the ws arm |

The only genuinely new code is **glue at the three boundaries**: the
FastMCP↔rebac `TokenVerifier`, the credential→actor verifier (agents), and the
ASGI dispatcher. Everything else is an owner's public API.

## Research findings (verified against the installed libs + the rebac repo)

- **Mount, don't wrap.** `streamable_http_app()` returns a Starlette app whose
  lifespan is `session_manager.run()` (`mcp server.py:1040`). The current Angee
  Django-view shim (`views.py` synthetic scope + buffered receive/send) and the
  per-`MCPServer` lazy task group (`runtime.py`) only exist because MCP was served
  through the URLconf. Mounting the app removes both.
- **Daphne 4.2.1 has no lifespan** (zero matches in source); channels'
  `ProtocolTypeRouter` raises on unknown scope types; Django `ASGIHandler` rejects
  non-`http`. → a single hand-written scope dispatcher (the fyltr `FyltrASGI`
  shape) that handles `lifespan`/`websocket`/`http`, with **lazy ensure-started**
  on first `/mcp` request (daphne path) and real lifespan handling (uvicorn path).
- **FastMCP supports authn**: `auth=AuthSettings(issuer_url=…, required_scopes=[])`
  + `token_verifier: TokenVerifier` (`verify_token(token) -> AccessToken | None`).
  It installs `AuthenticationMiddleware(BearerAuthBackend(...))` **and**
  `AuthContextMiddleware` (`server.py:866,871`) + `RequireAuthMiddleware`
  (→ `401 invalid_token`). So **`get_access_token()` works inside a tool body**,
  and the verifier's returned object flows through untouched
  (`AuthenticatedUser.access_token`) → a `RebacAccessToken(AccessToken)` subclass
  can carry the resolved `SubjectRef`.
- **rebac already specifies the MCP adapter** (`docs/proposals/0004-mcp-tool-integration.md`,
  status Draft, module absent): `rebac.mcp.rebac_mcp_tool(resource_type, action,
  id_arg=…)` resolves the actor from the request context, builds
  `ObjectRef(resource_type, resource_id)`, **checks the permission before the tool
  body** (deny → `rebac.PermissionDenied`), supports sync+async, and **explicitly
  must not mint/validate identity — authn stays at the transport.** This is the
  exact ownership split above.
- **rebac adapter conventions** (mirror these): `rebac.drf.RebacPermission` reads
  `current_actor()` then `backend().has_access(subject, action, resource)`;
  `RebacFilterBackend` does `queryset.with_actor(subject)`. `rebac.middleware.
  ActorMiddleware` is dual sync/async, resolver-driven (`REBAC_ACTOR_RESOLVER`,
  `chain_resolvers`, `bearer_token`), and brackets each request in
  `evaluator_scope` + `zookie_scope`. rebac exports `backend`, `check_new`,
  `PermissionDenied`, `to_object_ref`, `with_actor` — all the decorator needs.

## Target architecture

```
daphne (runserver, no lifespan)           uvicorn (sends lifespan)
        └──────────────┬───────────────────────────┘
                       ▼
          angee.asgi.application  — AngeeASGI dispatcher (replaces ProtocolTypeRouter)
            ├─ lifespan   → AsyncExitStack: enter each mounted app's session_manager.run()
            ├─ websocket  → AuthMiddlewareStack(URLRouter(ws_patterns))      [unchanged]
            ├─ http /mcp  → ensure-started(once) → FastMCP Starlette app
            │                ├ AuthenticationMiddleware(BearerAuthBackend(RebacTokenVerifier))
            │                ├ AuthContextMiddleware           (get_access_token works in tools)
            │                └ RequireAuthMiddleware           (401 on bad/absent bearer)
            └─ http *     → Django ASGI app                                   [unchanged]

tool body:  @mcp.tool
            @rebac_mcp_tool(resource_type="notes/note", action="read", id_arg="note_id")
            async def read_note(note_id, ctx): ...
            # decorator: actor = resolve(ctx) (from get_access_token());
            #            backend().has_access(actor, action, ObjectRef(type,id)) or PermissionDenied
```

Authn flow: agent sends `Authorization: Bearer <credential>` → `RebacTokenVerifier.
verify_token` (async; `sync_to_async` an **indexed** credential lookup +
`hmac.compare_digest`) → `RebacAccessToken(subject=<ref>)` or `None` (→401) →
`rebac_mcp_tool` reads it, checks the permission, scopes the body to the actor.

## Changes by level

### A. The MCP authz decorator — **DECIDED: implement at the owner (rebac); editing django-zed-rebac is approved**

New `src/rebac/mcp.py` (+ export from `rebac/__init__.py`), following the drf/graphql
adapter pattern and proposal 0004's spec/tests. Angee imports it directly (pin the
local/unreleased rebac in `pyproject.toml`/`uv.lock`). No throwaway Angee copy.

- `rebac_mcp_tool(*, resource_type, action, id_arg=None, resource_id=None,
  hide_id_arg=False)` decorator, sync+async aware.
- **Actor resolution: SDK-neutral + pluggable.** Default reads
  `ctx.request_context.meta["actor_subject"]` (the proposal's contract); allow a
  configurable resolver `REBAC_MCP_ACTOR_RESOLVER` (mirrors `REBAC_ACTOR_RESOLVER`)
  so a host can resolve from the transport's auth context instead. Angee plugs a
  resolver that calls FastMCP `get_access_token()` → `SubjectRef`.
  *(Recommended refinement to raise on the rebac side: also accept the ambient
  `current_actor()` like the DRF adapter, so an ASGI actor-middleware can bracket
  the request — most consistent with rebac's other surfaces.)*
- Build `ObjectRef(resource_type, resource_id or arg or "*")`; run
  `backend().has_access(subject, action, resource)` (and `check_new(...)` for
  create-style actions) **before** the body; deny → `PermissionDenied`; on allow,
  enter `actor_context(actor)` and call the body.
- Follow rebac's process: it's a Draft proposal — move it to implemented with
  README/ARCHITECTURE/ZED updates and the proposal's test list. This is a separate
  repo + release; Angee depends on the new rebac version.
- **Fallback if rebac can't ship in lockstep:** Angee ships a thin
  `angee.mcp.authz` that mirrors the same decorator against rebac's public
  `backend()/check_new/PermissionDenied`, clearly marked "lift target →
  rebac.mcp," and is deleted when rebac releases. (Avoids blocking, keeps one
  implementation to delete later.)

### B. Angee framework — `angee/asgi.py` (owns scope/protocol composition)

- **Generalize the addon seam.** Beside `_addon_websocket_urlpatterns`, add
  `_addon_http_mounts` reading `<addon>.asgi.http_mounts()` →
  `list[tuple[str, ASGIApp]]`. Reuse the existing addon iteration (don't duplicate).
- **Replace `ProtocolTypeRouter` with `AngeeASGI`** (the fyltr dispatcher,
  built from discovery — "takes the mcp servers as input" via the seam):
  - `lifespan`: `startup` → `await _ensure_started()` (enter every mount's
    `app.router.lifespan_context(app)` into a retained `AsyncExitStack`; on error
    send `lifespan.startup.failed`); `shutdown` → `aclose()`.
  - `websocket`: existing `AuthMiddlewareStack(URLRouter(...))`.
  - `http` & path under a mount prefix: `await _ensure_started()` (idempotent,
    lock-guarded, **raises** on failure — no silent deadlock), dispatch to the
    mounted app.
  - else: Django ASGI app.
  - `AsyncExitStack` + `started` flag live on the long-lived dispatcher → fixes the
    GC/deadlock blocker (django-review #1) by construction.
- No http mounts contributed → fall back to current behaviour (unchanged for
  non-MCP projects).

### C. Angee `mcp` addon — one server, mounted

- **Rename `runtime.py` → `server.py`** (`angee.mcp.server`). Idiomatic Django
  module name; reads as "the MCP server."
- **One process-wide `FastMCP`** built with `auth` + `token_verifier`; tools
  discovered from addons' `mcp_tools` manifest (keep that contract). `MCPServer`
  loses all lifecycle (`_running_manager`/`_run_forever`/`_started`/`_lock`/private
  `_session_manager` reset) — lifecycle now belongs to `angee/asgi.py`.
- New `addons/angee/mcp/asgi.py`:
  ```python
  def http_mounts():
      return [("/mcp/", mcp_server().streamable_http_app())]
  ```
  Set the server's `streamable_http_path` so the mount-stripped path matches
  (mount `/mcp/`, `streamable_http_path="/"`). *(VERIFY: one request; the single
  fiddly bit.)* List return keeps it count-agnostic for future per-server.
- New `RebacTokenVerifier` (TokenVerifier): `verify_token` → `sync_to_async`
  indexed credential lookup via `ANGEE_MCP_ACTOR_VERIFIER`, `hmac.compare_digest`,
  return `RebacAccessToken(subject=<SubjectRef str>)` or `None`. Replaces
  `mcp_verifier.py`'s O(n) decrypt scan + `==` (django-review #2).
- **Delete** `views.py` (synthetic scope shim) and `urls.py` (per-server Django
  routes). Trim `actors.py`: drop `_mcp_bearer_resolver`/`mcp_actor_resolver` and
  the `REBAC_ACTOR_RESOLVER` arm in `autoconfig.py` — MCP is off the Django request
  path now, so the event-loop-declining hack is gone. Keep only what the verifier
  reuses.
- The actor resolver Angee plugs into `rebac_mcp_tool` (step A): reads FastMCP
  `get_access_token()` → the `SubjectRef` off `RebacAccessToken`.

### D. Tool bodies — use the rebac decorator (deletes hand-rolled scoping)

- `examples/.../notes/mcp_tools.py`: replace `run_scoped`/`request_actor`/
  `system_context` with `@rebac_mcp_tool(resource_type=…, action=…, id_arg=…)`.
  This fixes the `create_note` `system_context` elevation (django-review #3): the
  decorator runs a real `check_new`/`has_access` before the body instead of sudo.
- Delete `addons/angee/mcp/tools.py` (`run_scoped`, `request_actor`,
  `SCOPE_ACTOR_KEY`) — its job moves to `rebac_mcp_tool` + the verifier.

### E. Max-DRY dedup (folded in per "maximum DRY")

- Extract the addon manifest **dotted-reference resolution** duplicated by
  `angee.graphql.schema` and `angee.mcp` (arch-review #1) into one owner
  (`angee.addons`), used by both the `schemas` and `mcp_tools` seams.

### F. Settings / composer / docs / tests

- `ASGI_APPLICATION` unchanged. `autoconfig`: keep `ANGEE_MCP_ACTOR_VERIFIER`;
  drop the `REBAC_ACTOR_RESOLVER` MCP arm; add an `issuer_url` base-URL setting.
- `docs/stack.md` `mcp (FastMCP)` row → "mounts the StreamableHTTP ASGI app at
  `/mcp` via the `asgi.py` seam; authenticates the bearer with a FastMCP
  `TokenVerifier`; authorizes tools with rebac `rebac_mcp_tool`." Note the new
  rebac min-version.
- Tests drive the **mounted** `angee.asgi.application` (ASGI test client): 401 on
  bad bearer; actor reaches the tool; lazy ensure-started under a no-lifespan
  server; actor-scoped reads; denied write blocked (proposal-0004 test list +
  Angee integration).

## Risks / verify

1. **Path alignment** (mount prefix vs `streamable_http_path`) — one request to confirm.
2. **Cross-repo sequencing** — `rebac.mcp` lands in `django-zed-rebac` and is
   released before Angee pins it; use the step-A fallback to avoid blocking.
3. **Actor-bridge contract** — decorator reads `get_access_token()` via the
   pluggable resolver; finalize the resolver signature with the rebac side
   (meta-string vs ambient `current_actor()` vs `ctx` resolver).
4. **`AuthSettings` minimal shape** — `issuer_url` required; token-verifier-only
   (no OAuth provider) serves without the registration/authorize routes
   (`server.py:875` only adds those for an `auth_server_provider`).
5. **Django stays a plain ASGI fallback** (not under a Starlette Mount) → no
   `root_path` surprises. Matches fyltr.

## Suggested sequencing (each verifiable against `angee dev`)

1. `angee/asgi.py` dispatcher + `http_mounts` seam; mount the existing server,
   delete `views.py`/`urls.py`/`runtime.py`→`server.py`. (Proves mount + lifecycle.)
2. `RebacTokenVerifier` + FastMCP `auth`; delete `mcp_verifier.py` scan. (Proves authn/401.)
3. `rebac.mcp.rebac_mcp_tool` (rebac repo) + swap `mcp_tools.py` to it; delete
   `tools.py`. (Proves authz + create-path fix.)
4. DRY dedup (manifest resolver) + docs + tests.

## Deletions (the "delete as much as possible" goal, made concrete)

- `addons/angee/mcp/views.py` — the synthetic-scope Django view shim (whole file).
- `addons/angee/mcp/urls.py` — per-server URLconf routes (whole file).
- `addons/angee/mcp/tools.py` — `run_scoped` / `request_actor` / `SCOPE_ACTOR_KEY`
  (whole file; replaced by `rebac_mcp_tool`).
- `addons/angee/agents/mcp_verifier.py` — the O(n) decrypt scan (whole file;
  replaced by an indexed `RebacTokenVerifier`).
- `runtime.py` → `server.py`, shedding `_running_manager`/`_run_forever`/
  `_started`/`_lock`/the private `_session_manager` reset and the per-request
  dispatch.
- `actors.py` — drop `_mcp_bearer_resolver`/`mcp_actor_resolver`; the
  `REBAC_ACTOR_RESOLVER` MCP arm in `autoconfig.py`.
- Agents `Agent.mcp_*` helpers slim down (move credential/header shape onto
  `MCPServer`, arch-review #2).

## Reviewer findings resolved by this change

- django #1 (runtime task GC/deadlock) — **deleted** (lifecycle moves to `angee/asgi.py`, entered once, retained).
- django #2 (full-table decrypt scan + non-constant-time compare) — **fixed** (indexed lookup + `hmac.compare_digest` in `RebacTokenVerifier`).
- django #3 (`create_note` `system_context` elevation) — **fixed** (`rebac_mcp_tool` runs `check_new`/`has_access` before the body).
- django #4 (placeholder agent-actor write-enabled) — **fixed** (fail-closed: no elevated write path remains).
- arch #1 (duplicated manifest dotted-reference resolution) — **fixed** (extract to `angee.addons`).
- arch #2/#3 (`MCPServer` shape decoded by `Agent`; `Any` typing) — **fixed** (behavior + types move onto `MCPServer`).
- arch #4 (private `_session_manager` reach) — **deleted** (use public `streamable_http_app()`/lifespan).
- arch #5 / react #3 (`expiresAt` unused / reconnect undocumented) — addressed in the frontend pass (implement or drop).
- django #5 (N+1 on `mcp_servers`) — `select_related("credential")`.
- django #6 (`render_view_context` unscoped preview) — scope or document.
- django #7 (`mint_route_token` dead default TTL) — make the chat TTL a setting.
- react #1,#2,#4,#5,#6,#7 — frontend pass (urql query-not-mutation, liveness guard,
  tool-call status, valibot boundary narrowing, exact permission-kind match).

## Out of scope

- Unifying the MCP credential bearer with the chat WS route-token
  (`mint_route_token`) — different directions; revisit only if you want one token.
```
