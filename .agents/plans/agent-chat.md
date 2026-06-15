# Agent chat (ACP) console + notes MCP + context — executable plan

A `-p` agent can run this end to end. Goal: **chat with a running agent in the
console**. The browser speaks ACP (Agent Client Protocol) to the agent's WebSocket
**through the operator's central Caddy**, forward-authed with an operator-minted,
service-scoped **route token**. The agent receives **workspace context** and the
**notes MCP server**. Green-field: no provenance, native idioms, DRY, defer to stack.

Decided dependencies (add `docs/stack.md` owner rows — Step 0):
- ACP client (TS): `@zed-industries/agent-client-protocol` (mirrors the agent image's `@zed-industries/claude-code-acp`).
- Chat UI (TS): `@assistant-ui/react` + `streamdown`.
- MCP server (Py): `mcp` (FastMCP).

## Verified central-Caddy auth (from the operator — build to this)

- A routed service `route: {port: 3007, auth: forward}` is fronted by the central Caddy,
  which `forward_auth`s every upgrade to the operator `GET /edge/verify?service=<service>`.
- `/edge/verify` reads the token from `?token=` (Caddy passes it via `X-Forwarded-Uri`),
  also accepts `Authorization: Bearer`/`Sec-WebSocket-Protocol`, and verifies it against
  audience `svc:<service>`. Browsers can't set ws headers → **token must ride in the URL query**.
- Route token: `POST /tokens/route` body `{actor, service, ttl}` → HMAC-SHA256 **JWT**,
  claims `sub=actor`, `iss=angee-operator`, `aud="svc:<service>"`, `exp` (default 1h, max 24h).
  This is a **different endpoint** from `/tokens/mint` (the operator-GraphQL token).
- Service URL: `GET /services/{name}/endpoint` → `{routed, url:"wss://<service>.<domain>/",
  internalHost, internalPort}` — URL carries **no** token.
- Browser opens: `new WebSocket(endpoint.url + "?token=" + routeToken)`.
- The service name is the rendered `agent.service` (e.g. `agent-demo-agent`); use it for
  both the endpoint lookup and the route token's `service`.

## Step 0 — `docs/stack.md` owner rows

Add three rows (one line each) under the relevant sections: the ACP client lib (owns the
agent JSON-RPC/ACP client), `@assistant-ui/react` + `streamdown` (owns the chat thread UI +
streamed-markdown render), `mcp`/FastMCP (owns the Python MCP server runtime). Add the TS
deps to the relevant `package.json` and `mcp` to `pyproject.toml`; refresh lockfiles.

## Step 1 — Backend chat endpoint (dependency-free; do first)

**`addons/angee/operator/daemon.py`** — two REST methods mirroring the existing ones:
- `mint_route_token(self, actor: str, service: str, ttl: str = "1h") -> dict[str, Any]`
  → `self._request("POST", f"{self._base()}/tokens/route", {"actor": actor, "service": service, "ttl": ttl})`.
- `service_endpoint(self, name: str) -> dict[str, Any]`
  → `self._request("GET", f"{self._base()}/services/{quote(name, safe='')}/endpoint")`.

**`addons/angee/agents/schema.py`** — new type + resolver on the agents schema:
- `@strawberry.type AgentChatEndpoint { url: str; token: str; expires_at: str; mcp_servers: JSON }`.
- Add to the agents **query** class (gated like other admin/owner reads):
  `agent_chat_endpoint(self, info, id: relay.GlobalID) -> AgentChatEndpoint`.
  - Resolve the agent (REBAC-scoped). If `not agent.service`: raise a typed error
    ("agent is not running"). 
  - `daemon = OperatorDaemon.from_settings()`; `ep = daemon.service_endpoint(agent.service)`;
    if not `ep.get("routed")`: error.
  - `actor` = the same actor identity `operatorConnection` mints with (see
    `operator/schema.py` — the session user's public id / `SubjectRef`).
  - `tok = daemon.mint_route_token(actor, agent.service, ttl="2h")`.
  - return `AgentChatEndpoint(url=ep["url"], token=tok["token"], expires_at=tok.get("expiresAt",""),
    mcp_servers=agent.mcp_config().get("mcpServers", {}))`.
- Keep the secret server-side discipline: the token is short-lived and per-actor; fine to
  return to the authorized browser (it's how the ws authenticates).

**SDL + tests:** `uv run examples/notes-angee/manage.py angee build` → `schema` → `schema --check`.
Add to `tests/test_agents_graphql.py`: a `_FakeDaemon` with `service_endpoint`/`mint_route_token`;
assert the resolver returns url+token+mcpServers, is admin/owner-gated, and errors when
`agent.service` is empty. Run `ruff`/`mypy`/`pytest`.

## Step 2 — Notes MCP server (FastMCP) + wiring — DONE

**Decided MCP-auth model (built to this):**
- `agents.MCPServer.credential` (an `iam.Credential`) holds the MCP **bearer**. `Agent.mcp_config()`
  emits each credentialed server's `headers: {"Authorization": "Bearer ${secret.<name>}"}` — an
  operator **secret reference**, never a file literal — and the provision flow syncs that value to
  the operator secret store (mirrors the inference-token sync). See `Agent.mcp_config` /
  `mcp_secret_name` / `mcp_secrets` and the `_RenderPlan.mcp_secrets` sync in `agents/schema.py`.
- A custom **`REBAC_ACTOR_RESOLVER`** (`angee.mcp.actors.mcp_actor_resolver`, built on
  `rebac.chain_resolvers` + `rebac.bearer_token`) verifies the inbound MCP bearer and resolves it
  to an **agent actor**, **no admin fallback**. The verifier is pluggable via
  `ANGEE_MCP_ACTOR_VERIFIER` (the agents addon supplies `angee.agents.mcp_verifier.resolve_actor`,
  matching the bearer to a server `credential` → an agent-scoped actor). Tool bodies run under that
  actor via `angee.mcp.tools.run_scoped` (binds `actor_context`, hops to a thread so the ORM is off
  the event loop). The sync resolver declines on the event loop (no sync ORM there); the async MCP
  view does the awaited verify and injects the actor into the ASGI scope.

**Two deferred `TODO` notes left in code (`addons/angee/agents/mcp_verifier.py`):**
1. `# TODO(agent-actor)` — the bearer must identify the **agent** actor, not the owning user.
   Today it resolves to a per-credential `agents/agent:<credential-sqid>` placeholder (a real,
   distinct, non-user subject that reads only what it is granted).
2. `# TODO(mcp-authz)` — the resolver/authz must also check the **agent** may use this server and
   which tools — deferred to the next slice.

**What was built:**
- **Base addon `angee.mcp`** (`addons/angee/mcp/`): `runtime.py` (per-server FastMCP +
  StreamableHTTP session manager held open per serving loop; `mcp_tools` manifest-attribute
  discovery seam), `views.py` (async Django view driving the session manager, actor in scope),
  `urls.py` (mounts `mcp/<server>/`), `actors.py` (the resolver chain), `tools.py` (`run_scoped`),
  `autoconfig.py` (`REBAC_ACTOR_RESOLVER`).
- **Notes tools (`example.notes.mcp_tools`):** `list_notes`/`read_note`/`create_note`/`update_note`
  over the notes models, scoped to the request actor; declared via `mcp_tools = "mcp_tools.register"`.
  (Module named `mcp_tools`, not `mcp` — an addon `mcp.py` that imports the third-party `mcp` is
  shadowed by `unittest` discovery; see `docs/backend/guidelines.md` Pitfalls.)
- **Verified:** ruff/mypy/pytest green; SDL clean; a direct StreamableHTTP `tools/list` +
  `tools/call` round trip lists/creates/reads a note under a test actor
  (`examples/notes-angee/addons/example/notes/tests/test_mcp.py`), plus the verifier +
  `${secret}`-header unit tests in `tests/test_agents_graphql.py`.

## Step 3 — Context resolver (agents base) — DONE

`renderAgentPrompt(id, view)` is a console **mutation** (admin-gated; resolves the agent
to confirm the caller may drive it), and the model-generic rendering lives in
`addons/angee/agents/context.py`. The view `type` is the **rebac resource type** (e.g.
`agents/mcp_server`), resolved via `rebac.resources.model_for_resource_type` (not
`apps.get_model`, whose `model_name` drops the underscore). Rows resolve through
`instance_from_public_id` (works for any manager), previewed under `system_context`,
capped, with a pointer to the MCP tools. Tested in `tests/test_agents_graphql.py`.

## Step 3 (original spec) — Context resolver (agents base)

`render_agent_prompt(self, info, id: relay.GlobalID, view: JSON) -> str` — build a
`<system_context>…</system_context>` text block from a **view envelope**
(`{kind: record|list|dashboard, type: "<app>/<model>", sqid?, sqids?, params?}`): the
view metadata + short previews of the selected rows (sudo read, cap ~20), and an instruction
to call the MCP tool for full bodies. Keep it model-generic (look up the model + a per-model
summary; default to a few public fields). The client calls this each send and prefixes it.

## Step 4 — Frontend ACP client + chat view (`addons/angee/agents/web`) — DONE (typecheck only; live wss path is `routed:false` locally)

- `documents.ts`: `AGENT_CHAT_ENDPOINT_QUERY` (id → {url, token, expiresAt, mcpServers}) and
  `RENDER_AGENT_PROMPT_MUTATION`.
- `acp-transport.ts`: `new WebSocket(url + "?token=" + token)`; ndjson framing (one JSON-RPC
  object per line; append `\n` on write); adapt to the ACP SDK's `ClientSideConnection`
  (readable/writable Stream over the ws). Reconnect/re-query the endpoint when the token nears
  expiry (it's ≤2h).
- `useAcpRuntime.ts`: `initialize({protocolVersion, clientInfo})` → `newSession({cwd:"/workspace",
  mcpServers})`; on send → call `RENDER_AGENT_PROMPT` then `prompt({sessionId, prompt:[{type:"text",
  text: context + userText}]})`; fold `session/update` chunks (`agent_message_chunk`/
  `agent_thought_chunk` → text; `tool_call`/`tool_call_update` → tool blocks) into an
  `@assistant-ui/react` runtime; `cancel({sessionId})`; auto-allow the first `allow*` on
  `requestPermission`. Hold messages as a mutable ref + immutable snapshot per update.
- `AgentChat.tsx`: `AssistantRuntimeProvider` + assistant-ui Thread primitives; assistant
  markdown via `streamdown`; tool-call fallback UI; a status header (idle|connecting|ready|
  error|closed) with the composer disabled until ready. Slot it at the **agent detail**
  (recordExtras on `AgentsPage`, beside `AgentProvisioning`) for non-template agents whose
  `status === RUNNING` and `service` is set — or a route `/agents/$id/chat`.
- Verify with `pnpm --filter @angee/agents typecheck`.

## Build order, checkpoints, verification

Order: 0 → 1 → 2 → 3 → 4. Verify each:
- **1:** resolver returns a `wss://…` URL + JWT; a raw ws probe to `url?token=<jwt>` is
  forward-authed (101) and rejected without/with a bad token (401).
- **2:** the agent's rendered `.mcp.json` lists the notes server; calling `read_note` returns a note.
- **3:** the rendered context reflects the open record/selection.
- **4:** open the chat → send → streamed reply renders; a tool call renders; context reflects
  the open record. Live test against a provisioned, RUNNING demo agent.
- Checks: `ruff`/`mypy`/`pytest` (backend), `pnpm typecheck` (web), `schema --check`.

## Notes / decisions surfaced for the human

- MCP auth model (Step 2 (a)/(b)) is the main open design point — confirm before wiring.
- assistant-ui may need a thin custom runtime adapter (ExternalStore) to bridge ACP streaming;
  budget for that in Step 4.
- Route-token TTL ≤ 2h → the chat ws needs reconnect-on-expiry (Step 4 transport).
