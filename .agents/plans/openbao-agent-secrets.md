# Plan: OpenBao-backed live agent secrets (eliminate the stale-token 401)

**Status:** research + design only — not implemented. Plan only.

## Context / the problem

A provisioned agent's `CLAUDE_CODE_OAUTH_TOKEN` is a **frozen snapshot**. At
provision/reprovision, Django calls `Agent.inference_secret()`
(`addons/angee/agents/models.py:636`) which `ensure_fresh()`es the OAuth credential
and pushes the value to the operator secret store via
`OperatorDaemon.set_secret(name, value)` (`addons/angee/operator/daemon.py:117`,
`POST /secrets/{name}`). The claude-code service template wires it into the container
env at **create time** — `service.yaml.jinja:17`:
`CLAUDE_CODE_OAUTH_TOKEN: "${secret.{{ secret_name }}}"`. There is **no background
refresh** (confirmed: `ensure_fresh` is only ever called inside provision/reprovision;
no cron/celery/scheduler exists). An OAuth token expires, the frozen copy goes stale,
and Anthropic returns `401 Invalid authentication credentials`. A restart reuses the
same baked env, so the only fix today is a manual **reprovision** (recreate the
service). The same frozen-snapshot limit forces MCP bearers to be **static-only**
(`models.py:306-310`).

Two distinct sub-problems:
- **(A) keep the token fresh** — refresh the OAuth access token before expiry.
- **(B) deliver the fresh token into the *running* container** without a recreate.

## What OpenBao gives us — and what it does not

OpenBao is the Linux-Foundation fork of HashiCorp Vault (forked from Vault 1.14),
API-compatible. Relevant capabilities:
- **KV v2** versioned secret store; **leases + renewal**; **dynamic secrets** engines
  (database, cloud, PKI) that mint short-lived creds and auto-revoke on lease end.
- **Auth methods**: AppRole, JWT/OIDC, Kubernetes, etc.
- **OpenBao Agent — process-supervisor mode** (the linchpin for **(B)**): it
  auto-auths, renders secrets from `env_template` blocks as **env vars into a child
  process** (`exec` block), and **`restart_on_secret_changes` (default `"always"`)
  restarts the child whenever an injected secret changes** (a KV update detected on
  `static_secret_render_interval`, or a dynamic secret nearing expiry). It waits for
  the first render before starting the child and forwards stdio.

**What OpenBao does NOT do:** it has no Anthropic engine, so it cannot perform the
Anthropic OAuth **refresh** (refresh-token → access-token). Sub-problem **(A)** stays
in Angee's `iam.Credential.ensure_fresh()` (`addons/angee/iam/models.py:1056`,
handler `OAuthCredentialHandler` in `iam/credentials.py`). **OpenBao solves (B)
— live delivery + auto-restart; Angee still owns (A).** A scheduled Angee job that
re-runs `ensure_fresh` and writes the new value into OpenBao is therefore still
required — OpenBao alone does not make the token fresh.

Sources: OpenBao Agent <https://openbao.org/docs/agent-and-proxy/agent/>,
process-supervisor <https://openbao.org/docs/agent-and-proxy/agent/process-supervisor/>,
templates <https://openbao.org/docs/agent-and-proxy/agent/template/>,
project <https://openbao.org/> / <https://github.com/openbao/openbao>.

## Target architecture

```
 Anthropic OAuth (refresh grant)
        ▲ refresh  (ensure_fresh)
        │
 Angee/Django ──(scheduled renewal job)──► write fresh access_token ─► OpenBao KV  secret/agents/<sqid>/inference
        │  provision: create KV path + per-agent policy + AppRole              │
        ▼                                                                      │ static_secret_render_interval
 Operator (Go) ── create service + inject OpenBao addr + wrapped secret_id     │ detects KV change
        ▼                                                                      ▼
 agent container:  bao agent  (process-supervisor)
        env_template CLAUDE_CODE_OAUTH_TOKEN ◄── reads KV
        exec: claude-code (ACP)              ── restart_on_secret_changes=always ─► restarts claude-code on rotation
```

Flow:
1. **Provision** — Angee writes the initial token to OpenBao KV (`secret/agents/<sqid>/inference`),
   creates a per-agent policy + AppRole scoped to that path, and asks the operator to
   create the service with the OpenBao address and a **response-wrapped, single-use
   AppRole `secret_id`** for bootstrap (secure introduction).
2. **Running** — the container's OpenBao Agent auto-auths (AppRole), renders the token
   into the claude-code child's env, and supervises the process.
3. **Renewal (the fix)** — a scheduled Angee job periodically `ensure_fresh()`es each
   provisioned OAuth credential and re-writes the fresh token to its KV path. OpenBao
   Agent detects the KV change and **restarts claude-code with the new token** — no
   service recreate, no 401. The same path relaxes the MCP static-cred constraint
   (MCP bearers become OpenBao-delivered with auto-restart too).

## Division of labor (this spans two repos)

- **Angee (this repo):**
  - A **scheduled renewal job**: for each provisioned agent with an OAuth-backed
    inference (or MCP) credential, `ensure_fresh()` then write the value to OpenBao
    (or re-`set_secret`). This is the single biggest Angee-side win and is what
    actually keeps the token fresh.
  - **Provision wiring** (`schema.py:_sync_secrets`/`_render_plan`): write to OpenBao +
    create the per-agent policy/AppRole instead of (or alongside) `daemon.set_secret`.
  - **Service template** (`templates/services/claude-code/.../service.yaml.jinja`):
    wrap the agent command in `bao agent` process-supervisor (config: auto-auth AppRole,
    `env_template` for `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` + MCP bearers,
    `exec` the real command, `restart_on_secret_changes=always`).
  - Optional **`vault_ref` credential kind** (`iam/credentials.py` already names this
    exact seam): a credential whose material is a pointer to an OpenBao path; Django
    holds no secret material. End-state, not required for phase 1.
  - **`docs/stack.md`**: add the secrets-management owner row (OpenBao). New
    framework-level capability → escalate to the architect per the constitution.
- **Operator (`angee-operator`, Go, out of this checkout):**
  - Stand up / manage an OpenBao service (dev-mode single node locally; sealed + real
    storage in prod).
  - Back the secret store with OpenBao **or** pass through; inject the OpenBao address +
    wrapped `secret_id` into the agent container; optionally run/ship the OpenBao Agent
    binary in the claude-code image (build context `./.angee/services/<name>/docker`).

## Phased plan (each phase independently verifiable)

- **Phase 0 — immediate relief, no OpenBao.** Add the **scheduled renewal job** that
  re-runs `ensure_fresh` and **reprovisions** the service before expiry (reuse
  `reprovision_agent`'s `_sync_secrets` + service recreate). Ship the interim
  **"Recreate service" button** (wire the existing `reprovisionAgent` mutation into
  `AgentProvisioning.tsx` — backend already exists). This kills the 401 today; OpenBao
  later makes the refresh lighter (process restart, not full service recreate).
- **Phase 1 — stand up OpenBao.** Operator runs a `bao` service (KV v2 at
  `secret/agents/`). No Angee behavior change yet; prove connectivity + a manual KV
  read from a throwaway container.
- **Phase 2 — Angee writes to OpenBao at provision.** `_sync_secrets` dual-writes the
  inference token to OpenBao KV and creates the per-agent policy + AppRole; operator
  injects the OpenBao addr + wrapped `secret_id`. Service still reads from env at create
  time (unchanged) — just sourced from OpenBao. Verifies write/auth/secure-introduction.
- **Phase 3 — OpenBao Agent wrapper + lighter renewal.** Wrap claude-code in `bao agent`
  process-supervisor (`env_template` + `exec` + `restart_on_secret_changes`). Switch the
  Phase-0 scheduled job from "reprovision" to "ensure_fresh + KV write" (no service
  recreate — OpenBao Agent restarts the process). **This is where the recreate
  disappears.**
- **Phase 4 — generalize.** Extend to MCP bearers (relax the static-cred constraint at
  the catalogue), add the `vault_ref` credential kind (Django stops storing rotating
  material), wire audit/leases, and write `docs/stack.md` + a backend Pitfalls note.

## Key integration points (seams, with locations)

| Concern | Owner / seam | Change |
|---|---|---|
| Push secret to store | `OperatorDaemon.set_secret` (`operator/daemon.py:117`) | add an OpenBao write (or operator backs its store with OpenBao) |
| Gather + freshen secret | `Agent.inference_secret`/`mcp_secrets` (`agents/models.py:636,612`), `Credential.ensure_fresh` (`iam/models.py:1056`) | call from a scheduled job, not only provision |
| Provision flow | `_sync_secrets`/`_render_plan`/`_render_service` (`agents/schema.py:644,619,608`) | write KV + create policy/AppRole; pass OpenBao bootstrap to the operator |
| Container env wiring | `service.yaml.jinja:17` | wrap in `bao agent` process-supervisor |
| Credential material | `vault_ref` seam (`iam/credentials.py:2-6`) | optional new `CredentialKindHandler` |
| Periodic task runner | **none today** | identify the owner — the `integrate` addon appears to have a scheduler (`tests/test_integrate_scheduler.py`); confirm it's general-purpose, else add one (APScheduler / operator cron) |

## Decisions to make before building

1. **Where OpenBao lives in the ownership model** — operator-owned (operator backs its
   secret store with OpenBao; Django keeps calling `set_secret`) **vs** Angee-direct
   (Django talks to OpenBao; operator only injects bootstrap). Recommend **operator-owned**:
   the operator already owns secret storage + container provisioning, so it's the
   find-the-owner fit and keeps Django decoupled from the secrets backend.
2. **Secure introduction (bootstrap auth)** — how the container first authenticates to
   OpenBao. Recommend **AppRole with a response-wrapped, single-use `secret_id`** the
   operator unwraps/injects at create; OpenBao Agent then auto-renews its own token. The
   bootstrap cred is the only thing that must be delivered at create time, and it is
   non-rotating/short-lived — it does not have the OAuth staleness problem.
3. **`vault_ref` now or later** — dual-write (Django owns material, OpenBao delivers) is
   the pragmatic Phase-2 intermediate; `vault_ref` (OpenBao owns material) is the secure
   end-state. Don't block Phase 3 on it.
4. **Scheduler owner** — reuse vs add (see table).
5. **Dev vs prod OpenBao** — dev-mode unsealed single node locally; prod needs real
   seal/unseal + storage + HA. Out of scope for the demo but name it.

## Risks / trade-offs

- **Operational weight** — OpenBao is a stateful service (seal/unseal, storage, HA).
  Justified only if Angee wants a real secrets backend (audit, leases, dynamic DB/cloud
  creds, central rotation), not just this one token. Frame it as the strategic secrets
  owner, not a point fix.
- **Two-repo change** — most of the heavy lifting (OpenBao service, store backend,
  bootstrap injection, shipping the agent binary) is in the Go operator repo, outside
  this checkout. The Angee-side change is the scheduled job + template + provision wiring.
- **OpenBao ≠ OAuth broker** — Angee still performs the Anthropic refresh; OpenBao is
  delivery + auto-restart. (A future custom OpenBao secrets-engine *plugin* could broker
  Anthropic OAuth and mint leased access tokens, making refresh fully OpenBao-owned —
  big Go build; note as advanced/future.)
- **Restart drops the live chat** — when OpenBao Agent restarts claude-code on rotation,
  any active ACP session drops and the chat WS reconnects. Rare (only on rotation) and
  far better than a silent 401, but note it.
- **Bootstrap-secret handling** — a mishandled `secret_id` is a credential leak; the
  wrapped/single-use pattern mitigates it but must be done carefully (operator-side).

## Alternatives considered

- **Phase 0 alone (scheduled reprovision, no OpenBao)** — already kills the 401 by
  automating the recreate. Cheapest. Downsides: a full service destroy+create per
  rotation (heavier, brief downtime), and it keeps the frozen-snapshot model + the MCP
  static-cred constraint. Good immediate step; OpenBao is the durable upgrade.
- **File-mount + in-container watch** — operator writes the token to a mounted file the
  agent re-reads; needs the agent to support file-sourced creds + reload. Lighter than
  OpenBao but bespoke, no audit/leases, and Claude Code reads env not files.
- **Custom OpenBao Anthropic plugin** — fully OpenBao-owned OAuth brokering with leases;
  the cleanest end-state but a large Go plugin effort.

## Verification (per phase)

- **P0:** force an OAuth credential near expiry; confirm the scheduled job refreshes +
  reprovisions and the agent answers without a manual recreate; the new "Recreate
  service" button drives `reprovisionAgent` and the chat recovers.
- **P1:** `bao kv put/get secret/agents/test` from a throwaway container against the
  stack's OpenBao.
- **P2:** provision an agent; confirm the token lands in OpenBao KV, the per-agent
  AppRole policy reads only its path, and the container boots from the OpenBao-sourced env.
- **P3:** with the agent running, write a new token to its KV path; confirm OpenBao Agent
  restarts claude-code (logs) and the next prompt uses the new token — **no reprovision**.
  Simulate expiry end-to-end: scheduled `ensure_fresh` → KV write → auto-restart → 200.
- **P4:** an OAuth-backed MCP server stays authenticated across a rotation; `vault_ref`
  credential resolves live; audit log shows reads.
```
