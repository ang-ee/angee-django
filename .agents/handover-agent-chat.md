# Handover — MCP mount refactor + agent chat end-to-end

Workspace branch: `workspace/agents-addon`. Sibling repos in play:
`/Users/alexis/Work/angee/django-zed-rebac` (the `rebac` lib) and
`/Users/alexis/Work/angee/angee-operator` (the Go `angee` operator daemon).

This session did three things, in order: (1) reviewed the uncommitted MCP work and
refactored it, (2) integrated rebac's MCP authz + async ORM, (3) drove the agent
chat to connect end-to-end (ingress, TLS, transport, ACP handshake). Nothing is
committed.

## IMMEDIATE STATE — the chat is one test away

The chat now reaches the ACP handshake. Latest agent-log progression after fixes:
`initialize` ✓ → (framing fixed) → we tried `authenticate` → **claude-code-acp
returns `-32603 Method not implemented`** for `authenticate`. It only *advertises*
`authMethods: [claude-login]` as a terminal hint; it actually authenticates from
the **env token** `CLAUDE_CODE_OAUTH_TOKEN` (confirmed present in the container,
108 chars). So the just-applied fix removes the `authenticate` call and goes
straight to `newSession`.

**Next action: reload the chat and read the agent log.** Expected:
`initialize` → `session/new` → session live → chat works. If `newSession` errors,
the catch surfaces it (no hang). Files: `addons/angee/agents/web/src/useAcpRuntime.ts`
(connect flow) and `acp-transport.ts` (ndjson framing).

Caveat that bit us repeatedly: **confirm the `@angee/agents` bundle actually
reaches the browser** (hard-reload, cache off). An unchanged agent log across edits
meant a stale frontend, not a code problem.

## Completed + verified

Backend suites green: framework `uv run pytest tests/ -q` = **354 passed**, notes
`uv run examples/notes-angee/manage.py test example.notes` = **38 passed**,
`schema --check` ok, ruff + mypy clean on touched files, frontend `pnpm --filter
./addons/angee/agents/web run typecheck` clean. (Re-run after the last frontend
edit + any commit.)

1. **MCP server → ASGI mount.** One process-wide FastMCP server mounted at `/mcp`
   via `angee/asgi.py` (channels `ProtocolTypeRouter`: http prefix-router → mount
   else Django, websocket unchanged, lifespan enters `session_manager.run()` via
   `AsyncExitStack`). Deleted `addons/angee/mcp/{runtime,views,urls}.py`; new
   `server.py`/`verifier.py`/`asgi.py`/`actors.py`/`tools.py`. Daemon switched to
   **uvicorn** (sends ASGI lifespan; daphne didn't). See `.agents/plans/mcp-asgi-mount.md`.
2. **Auth split:** FastMCP `TokenVerifier` (`RebacTokenVerifier`) does bearer→actor
   (constant-time compare); rebac's `rebac_mcp_tool` does actor→permission. The
   verifier is the agents addon's, named by `ANGEE_MCP_ACTOR_VERIFIER`.
3. **rebac.mcp adopted** (implemented in the rebac repo, proposal 0004). Notes
   tools: `read_note`/`update_note` use `@rebac_mcp_tool`; `create_note` uses it +
   `create = authenticated` (schema rev 2); `list_notes` uses queryset scoping.
4. **rebac fix (in django-zed-rebac):** `LocalBackend._check_access` empty-id path
   now evaluates built-in actor terms (`authenticated`) before the `_accessible`
   fallback — so `create = authenticated` authorizes. (Made directly, no rebac
   tests run per request.)
5. **Async ORM:** dropped all `sync_to_async`/`run_scoped` thread hops — tools use
   `async for`/`afrom_public_id`/`acreate`/`asave` (rebac scopes async now). Added
   `AngeeQuerySet.afrom_public_id` in `angee/base/models.py`.
6. **Reviewer findings** (from the 3 review agents): all backend ones fixed
   (deadlock deleted, O(n) scan→indexed+hmac, create elevation→preflight, DRY
   `resolve_addon_reference` shared by graphql+mcp seams, `MCPServer.is_addressable`/
   `config_entry`, `select_related`, `ANGEE_AGENT_CHAT_TOKEN_TTL` setting,
   `render_view_context` scoped to actor). All 7 React findings fixed (query→mutation,
   liveness guard, expiry reconnect, tool-call status, valibot boundary, exact
   permission-kind, cast→guard).
7. **Pre-existing test failures fixed** (5): integrate test models → `tests/conftest.py`,
   stale `displayName`/oauth/app-order assertions, obsolete word_count migration test removed.
8. **Provisioning lifecycle (this session's tail):**
   - `daemon.destroy_service` (wraps operator `POST /services/{name}/destroy`).
   - `deprovision_agent` now destroys **service + workspace** (fixes
     `HTTP 409: service … already exists` on reprovision).
   - **`reprovision_agent`** (new mutation): re-syncs secrets + destroys + recreates
     the service over the **same workspace** — the way a credential/secret change
     takes effect (service env resolves `${secret}` at create, not restart).
   - DRY helpers `_render_plan` / `_sync_secrets`.
9. **Ingress (dev):** the operator shipped `ingress.routing: host|path` + `tls:
   auto|off`. Dev stack uses **`routing: path` + `tls: "off"` + `domain: localhost`**
   → `ws://localhost/<service>/` (no per-agent DNS, no cert trust). Operator binds
   `0.0.0.0` so the edge container's forward_auth reaches the local daemon. Applied
   to the dev stack template and active `.angee/angee.yaml`. New operator proposal:
   `angee-operator/docs/proposals/ingress-routing-modes.md`.
10. **Transport diagnostics:** `acp-transport.ts` decodes WS close codes
    (1006/1008/1015…) to UI + console instead of "agent WebSocket error", and
    re-adds the ndjson newline on incoming frames so `initialize` resolves.

## Open / not done

- **Verify the chat connects** (the immediate state above) — the only thing between
  here and a working demo, pending a fresh-bundle reload + the `newSession` log.
- **Cross-repo dependency pin:** `pyproject.toml` points `django-zed-rebac` at the
  **local editable checkout** (`[tool.uv.sources]`) because the rebac
  `_check_access` fix isn't published. Switch back to `>=0.11` (published) once that
  fix ships in a release. rebac 0.11 already has `rebac.mcp`.
- **Operator-side:** the `ingress-routing-modes.md` proposal (host vs path) is
  written but path-mode label-gen in the operator is the operator team's to confirm.
- **rebac fix untested** in the rebac repo (per the user's "no tests" instruction) —
  run rebac's suite before re-publishing.
- **Console UI:** a "Reprovision" action wired to the `reprovisionAgent` mutation
  would give users the credential-change path from the agents console.

## Commit / verify

Nothing committed. Before committing: re-run the three suites above + both
typechecks, then `git` on this workspace branch (never `git checkout` inside a
workspace). The rebac + operator repos have their own uncommitted changes that need
their own commits/releases. Plans: `.agents/plans/mcp-asgi-mount.md` and
`.agents/plans/agent-chat.md`.
