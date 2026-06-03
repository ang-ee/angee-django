# Service-template Caddy teardown → operator edge ingress

**Status:** Ready (unblocked) · **Pairs with:**
[`.agents/notes/edge-ingress-host-side.md`](../notes/edge-ingress-host-side.md)
and `../angee/docs/proposals/edge-ingress-caddy.md`.

Remove the per-agent Caddy + `verify-acp-token.mjs` sidecar from the agent
service templates and front them with the **operator's central Caddy edge**
(shipped in angee-go **v0.5.8**). Each agent service drops its host port and the
per-agent HMAC secret, runs only `stdio-to-ws`, and declares a `route:` stanza;
the edge's `forward_auth → GET /edge/verify` does auth centrally with an
operator-minted route token (`aud=svc:<service>`).

**Net effect:** *N* per-agent sidecars → one edge; per-agent HMAC secret →
operator-owned key; compose-port-scraping → `serviceEndpoint`. The browser wire
seam (`agentAcpEndpoint.{endpoint,token}` + `?token=`) is **unchanged**.

## Operator primitives this consumes (all shipped, v0.5.8)
- `ingress.type: caddy` in the stack manifest → caddy-docker-proxy edge; routed
  services drop host ports + take **no** `operator.port_pool` lease.
- `GET /edge/verify?service=<name>` — forward_auth target (200/401, never 101).
- `POST /tokens/route {actor, service, ttl}` → `aud=svc:<service>` route token.
- `serviceEndpoint(name)` → `{routed, url (wss://…), internalHost, internalPort}`.

## State / checklist

- **Legend:** `[ ]` todo · `[x]` done

| # | Build | Verify | Item |
|---|---|---|---|
| 1 | [ ] | [ ] | Stack template: add `ingress: caddy` + `INGRESS_DOMAIN` input |
| 2 | [ ] | [ ] | `claude-code` template: `route:` swap + drop host port/pool |
| 3 | [ ] | [ ] | `claude-code` docker/: delete Caddyfile + verifier; Dockerfile runs `stdio-to-ws` only |
| 4 | [ ] | [ ] | New `opencode` template mirroring claude-code |
| 5 | [ ] | [ ] | Django `agentAcpEndpoint`: route-token mint + `serviceEndpoint` |
| 6 | [ ] | [ ] | Django `operatorConnection`: scoped connection-token mint |
| 7 | [ ] | [ ] | Drop `Agent.acp_auth_secret` (model + migration + wizard) |

---

## 1. Stack template — enable the edge
`templates/stacks/dev/template/{{ ANGEE_ROOT }}/angee.yaml.jinja`
```jinja
ingress:
  type: caddy
  domain: {{ INGRESS_DOMAIN }}     # e.g. agents.localhost in dev
```
Add an `INGRESS_DOMAIN` copier input (default `agents.localhost`). **Verify:**
`angee stack prepare` renders a compose with the edge service (one published
port) and the `<name>_edge` network; `ingress.type: none` stacks are unchanged.

## 2. `claude-code` service template — the core swap
`templates/services/claude-code/template/service.yaml.jinja`
```jinja
services:
  {{ service_name }}:
    runtime: container
    build: { context: ./.angee/services/{{ service_name }}/docker }
    command: ["stdio-to-ws", "--port", "3008", "--", "claude-code", "acp"]
    mounts: [ "workspace://{{ workspace_name }}:/workspace" ]
    env: { AUTH_MODE: "{{ auth_mode }}", ACP_LOG_LEVEL: info }
    route:
      port: 3008
      auth: forward          # forward_auth → operator /edge/verify
```
- **Delete** `ports: ["{{ alloc_acp }}:3007"]` and all `ACP_AUTH_SECRET` env.
- `copier.yml`: **drop** `_angee.ensure.operator.port_pool.acp` (routed services
  take no host-port lease). Keep `name_pattern: agent-${workspace.name}` + inputs.

**Verify:** rendered `angee.yaml` fragment has `route:` and no `ports:`;
`angee service create` takes **no** `acp` lease (operator-side lease-skip);
`compile` strips host ports and stamps `caddy.forward_auth` +
`caddy.forward_auth.uri /edge/verify?service=<name>`.

## 3. `claude-code` docker/ — remove the sidecar
- **Delete** `template/docker/Caddyfile` and `template/docker/verify-acp-token.mjs`.
- `template/docker/Dockerfile`: remove the Caddy install + verifier; the image
  runs only `stdio-to-ws` + the `claude-code` CLI on `3008` (internal).

**Verify:** the built image has no Caddy; the container is unreachable except
through the edge.

## 4. New `opencode` service template
`templates/services/opencode/` mirroring claude-code, differing only in the
command (`stdio-to-ws --port 3008 -- opencode acp` or its ACP entrypoint) and
image/Dockerfile. Same `route: { port: 3008, auth: forward }`, no Caddy, no pool.

**Verify:** an opencode agent chats end-to-end through the edge, same as
claude-code.

## 5–7. Host-side Django (the consuming half — see the note for line refs)
- **`agentAcpEndpoint`**: replace `_mint_acp_token` (HMAC) with `POST /tokens/route
  {actor, service: agent.operator_service_name, ttl: 60s}`; replace
  compose-scraping with `operator.serviceEndpoint(name)`. Same `{endpoint, token}`
  return shape. Delete `_mint_acp_token`, `_operator_compose_for_acp_endpoint`,
  `_host_port_for_container_port`, `_fetch_operator_compose`, `_COMPOSE_CACHE`,
  `_ACP_SECRET_RE`, `_ACP_TOKEN_TTL_SECONDS`.
- **`operatorConnection`**: `POST /tokens/mint {actor, scope, ttl}` over the admin
  bearer instead of returning the raw bearer. Same `{endpoint, token}` shape.
- **`Agent.acp_auth_secret`**: drop the field (+ migration) and the wizard's
  `generateHmacSecret`/`acp-auth-secret` plumbing. `ANGEE_OPERATOR_TOKEN` becomes
  a server-side-only minting credential — audit no resolver returns it.

**Verify:** `agentAcpEndpoint` returns a `wss://…` edge URL + a route token; chat
opens end-to-end; the admin bearer never reaches the browser.

## Sequencing
1 → 2/3 (claude-code) → 5/6 (Django mint) → chat works → 7 (delete secret) → 4
(opencode). Validate each with the run-spike pattern (WS upgrade → 101 with a
minted route token, 401 without). Frontend (`AgentChatter.tsx`,
`acp-websocket.ts`) is untouched — the seam is preserved.
