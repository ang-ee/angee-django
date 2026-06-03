# Edge ingress — host-side (Django) companion

Host-side counterpart to the operator proposal
`../angee/docs/proposals/edge-ingress-caddy.md` (in the angee-go repo). That
proposal adds an optional `ingress` edge backend to the operator and one minted,
scoped-token mechanism shared by the edge **and** the operator API. This note
captures the reciprocal Django changes.

**Governing principle: keep the seam, swap the internals.** `agentAcpEndpoint`
and `operatorConnection` stay exactly as they are on the GraphQL surface — same
field names, same return types (`{ endpoint, token }`). We do **not** replace or
rename them. Only their *implementations* change: instead of returning the raw
admin bearer / a per-agent HMAC token, they return an **operator-minted,
short-lived, scoped token**. The browser code that consumes them does not change.

> **Daemon side is fully shipped (angee-go v0.5.8).** The minting + verify
> mechanism: REST `POST /tokens/mint` (`MintConnection(actor, scope, ttl)` →
> `aud="operator"` with scope) and `POST /tokens/route` (`MintRoute(actor,
> service, ttl)` → `aud="svc:<service>"`, no scope); a `Verify(raw, audience)`;
> two-tier `auth` (admin bearer = full, else a minted `aud="operator"` token
> whose actor + scope land in an `actorScope` context); a `graphql-ws` WebSocket
> transport on `/graphql`; **and now the `ingress` edge backend (Part 1) plus the
> route-token forward_auth endpoint**: `ingress.type: caddy` compiles a
> caddy-docker-proxy edge, `GET /edge/verify?service=<name>` validates a route
> token via `Verify(raw, serviceAudience(name))` (reads the token from
> `X-Forwarded-Uri`/`?token=`/`Authorization`/`Sec-WebSocket-Protocol`,
> 200/401 never 101), and `serviceEndpoint(name)` returns the public `wss://`
> URL. Validated end-to-end against a live `caddy-docker-proxy` run-spike.
> **Nothing is missing operator-side now** — the host-side changes below (and the
> service-template teardown, see
> [`.agents/plans/service-template-caddy-teardown.md`](../plans/service-template-caddy-teardown.md))
> are unblocked.

---

## The trust chain (after)

```
browser ──(session auth)──► Django
                              │  1. REBAC authz (does actor have this capability?)
                              │  2. mint via operator, over the ADMIN BEARER (server-side only)
                              ▼
                           operator.mintRouteToken / mintConnectionToken
                              │  returns short-lived, scoped JWT
                              ▼
browser ◄──{ endpoint, token }── Django        # the existing seam, unchanged shape
   │
   └─(wss/https + ?token=)─► edge / operator API ──► verifyToken (operator owns the key)
```

`ANGEE_OPERATOR_TOKEN` (the admin bearer) **never leaves the Django server**. The
browser only ever holds tokens scoped to exactly what the actor was authorized
for, expiring in seconds-to-minutes. Django = policy (REBAC, whether to mint);
operator = mechanism (mint, route, verify).

---

## Change 1 — `operatorConnection` internals (operator addon)

`src/angee/operator/graphql.py` — `resolve_operator_connection` (today returns
`token = operator_token()`, the raw admin bearer, gated only by a REBAC read
check).

Keep `OperatorConnectionInfo { endpoint, token }`. New internals:

1. REBAC-check `operator/connection#read` (as today).
2. Compute the actor's **operator scope** from their *effective* role membership
   (see [Scope map](#scope-map)).
3. `POST /tokens/mint` with `{actor: <sqid>, scope: <scopes>, ttl}` over the admin
   bearer held in settings (the shipped REST endpoint).
4. Return the minted token + the endpoint (the operator's edge URL, or its direct
   URL if the operator API isn't itself routed — the resolver just returns
   whatever the operator reports; it doesn't care which).

`operator_token()` stays in `conf.py` but is now used **only** server-side as the
minting credential — it is no longer a value any resolver returns to the browser.

No change to `OperatorSectionFrame.tsx` / `operatorFetch` callers: they still read
`operatorConnection.{endpoint,token}` and send `Authorization: Bearer <token>`.
The operator API now accepts that minted token (two-tier auth in the proposal).

## Change 2 — `agentAcpEndpoint` internals (agents addon)

`addons/agents/angee/agents/graphql/contribution.py` — `_resolve_agent_acp_endpoint`
(`:562`). Keep `AgentAcpEndpoint { endpoint, sessionPath, wsScheme, token,
tokenExpiresAt }`. New internals:

1. REBAC-check `chat` on `agents/agent` (as today).
2. Replace `_mint_acp_token` (`:611`, the per-agent HMAC) with
   `POST /tokens/route` `{actor: <sqid>, service: agent.operator_service_name,
   ttl: 60s}` (the shipped REST endpoint; route tokens carry **no scope** — the
   `chat` check in step 1 is the entire authorization).
3. Replace the compose-scraping endpoint resolution
   (`_operator_compose_for_acp_endpoint` `:653` + `_host_port_for_container_port`
   `:735`) with `operator.serviceEndpoint(name=agent.operator_service_name)` →
   public `wss://…` URL.

**Delete** once the above lands: `_mint_acp_token`, `_operator_compose_for_acp_endpoint`,
`_host_port_for_container_port`, `_fetch_operator_compose`, the `_COMPOSE_CACHE`,
and the `_ACP_SECRET_RE` / `_ACP_TOKEN_TTL_SECONDS` constants. This is a net
deletion — the resolver becomes "authz → mint route token → return operator
endpoint."

Extra hop note: chat-open now does Django→operator (`mintRouteToken` +
`serviceEndpoint`) instead of computing locally. Chat-open is not a hot path;
acceptable. `serviceEndpoint` is cacheable per service (the URL is stable between
stack reconfigurations).

## Change 3 — drop the per-agent ACP secret

The operator owns the signing key now, so the per-agent shared secret disappears
end to end:

- `addons/agents/angee/agents/models.py:427` — remove `Agent.acp_auth_secret`
  (`EncryptedTextField`). Migration to drop the column.
- `contribution.py` — remove the `acpAuthSecret` input on `AgentInstantiateInput`
  (`:207`), its validation (`:256`), and the `acp_auth_secret=` create kwarg
  (`:297`).
- `AgentSetupView.tsx` — remove `generateHmacSecret()` (`:336`), the
  `acp-auth-secret` `secretSet` call (`:342–345`), the `secret_name_acp_auth`
  service input (`:368`), and `acpAuthSecret` / `acp_auth_secret_name` on the
  `agentInstantiate` call (`:388`, `:395`). This is the only wizard-side change
  (provisioning, not chat).

## Change 4 — service-template teardown

`templates/services/claude-code/` — the agent service no longer authenticates
itself; the network boundary + central edge do:

- Delete `template/docker/Caddyfile` and `template/docker/verify-acp-token.mjs`.
- Drop the `:3007` host publish and all `ACP_AUTH_SECRET` plumbing.
- The service runs only `stdio-to-ws` on an internal port (3008) and declares a
  `route:` stanza in its rendered `angee.yaml` fragment (per the proposal's
  "service template after" example).

## Frontend: no chat-path changes

`AgentChatter.tsx` (consumes `agentAcpEndpoint.{endpoint,token}`, `:104–105`),
`acp-runtime.ts`, and `acp-websocket.ts` (`withToken` → `?token=`, `:175`) are
**unchanged**. Keeping the seam is precisely what buys this: the token's
provenance changes server-side; the wire contract the browser sees does not.

---

## Scope map (how REBAC roles bind to the token)

The two token types use roles differently:

- **Route token** (`agentAcpEndpoint`) — **no scope**. `aud="svc:<service>"` is the
  whole authorization; the per-instance REBAC `chat` check on the `agents/agent`
  at mint time is the only gate. Roles don't project into it.
- **Connection token** (`operatorConnection`) — carries `scope: []string`, which
  Django derives from the actor's **effective** role membership.

**Derive scope from `effective_member`, not `roles_of`.** Per the IAM roles plan
(`.agents/plans/iam-roles-lift.md`):

- **D7** makes `roles_of(actor)` / `currentUser.roleRefs` a *direct-grants-only UX
  hint* — "the server stays the authz boundary." Minting a scoped token **is** an
  authz decision, so it must check **effective** membership against
  `<ns>/role:<id>#effective_member` (the **D3** hierarchy folds `includes` in).
  Projecting scope off `roles_of`/`roleRefs` would drop hierarchy-implied roles
  (e.g. an `agent_admin` that `includes` `agent_operator` would silently lose the
  operator scopes) — an under-grant bug. Use the authz boundary, not the menu hint.
- **Roles live in per-addon catalogs** (plan §5): the operator addon ships
  `operator/role:*`, the agents addon ships `agents/role:agent_operator` /
  `agent_admin`. **None exist in this repo yet** — they land with those addon
  lifts, so the table below is forward-looking, owned by the operator/agents
  addons (not by this note), authored once, default-deny.
- Platform admins keep reach via the const-backed
  `admin: angee/role //rebac:const=admin` line ([[const-backed-relations]]).

Forward-looking projection (operator-addon-owned when lifted):

| Effective role membership | Operator scope granted |
|---|---|
| base (`operator/connection#read`) | `service:read`, `template:read`, `secret:read` |
| `operator/role:stack_operator` (or `agents/role:agent_operator`) | + `service:up/start/stop`, `secret:set`, `workspace:create`, `service:create` |
| `operator/role:operator_admin` (or `agents/role:agent_admin`) | + broad service/workspace/secret management |

The operator stays REBAC-ignorant: it enforces the flat `scope` per-field as
defense-in-depth. Fine-grained per-instance gating ("only services you own")
stays in Django at mint time — the operator scope is the coarse capability class.

## Settings

- `ANGEE_OPERATOR_TOKEN` — unchanged in shape, now **server-side minting
  credential only**. Audit that no resolver returns it.
- No per-agent secret settings (Change 3 removes them).

## Open questions

- **Scope→capability granularity.** The map above is coarse. "owned service"
  scoping (operator token may only act on services the actor owns) needs either a
  claim listing service names or an operator-side ownership check; decide with the
  operator proposal's scope→field map.
- **Operator endpoint exposure.** If the operator API is itself routed through the
  edge, `operatorConnection.endpoint` is the edge URL and `/edge/verify` gates it;
  if not, the operator's own auth middleware validates the minted token. The
  resolver is agnostic — but pick one for dev so `angee dev` wiring is concrete.

## See also

- `../angee/docs/proposals/edge-ingress-caddy.md` — the operator proposal this
  pairs with (mechanism: edge backend + minted scoped tokens).
- The operator daemon-bridge + console lift (operator-lift plan) — this note
  assumes those shared primitives once that plan is written.
- `addons/agents/docs/DESIGN.md` § ACP transport auth — the current per-container
  scheme being torn down.
