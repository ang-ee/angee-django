# Dependency Upgrade Plan — upgrade everything except the Strawberry forks

> **Status: EXECUTED & VERIFIED — 2026-06-26, branch `chore/dependency-upgrades`.**
> Stages 1 and 2 are done and green; Stage 3 stays upstream-blocked (a decision,
> not work). Full outcome at the bottom (`## Execution Outcome`).

Goal: bring every stale Python and JS/TS dependency to latest, **except** the two
intentional private Strawberry forks, which stay pinned because they carry
unmerged Angee features/fixes:

- `strawberry-graphql` → `ssh://…ang-ee/strawberry@codex/input-object-extensions`
- `strawberry-graphql-django` → `ssh://…ang-ee/strawberry-django@hotfix-angee-django`

These two are held deliberately (see `pyproject.toml` `[tool.uv.sources]` comment).
Do not touch them. `graphql-core` (a transitive of the fork) may also be gated by
the fork's `requires-dist`; treat it as opportunistic, not required.

Already done — do **not** re-do:
- ACP package rename `@zed-industries/agent-client-protocol` →
  `@agentclientprotocol/sdk@1.0.0` is complete (commit `e0066e6b`); lockfile,
  source imports, and `docs/stack.md` are all in sync. The earlier "deprecated,
  needs migration" finding was stale.

Verification gates (from `CLAUDE.md` / `docs/*/guidelines.md`), run after each stage:
- Backend: `uv run examples/notes-angee/manage.py angee build` → `makemigrations`
  (expect **no new migrations** from a dependency bump) → `migrate` → `pytest` →
  `manage.py schema --check`.
- Frontend: `pnpm install` → typecheck/build → `vitest` → storybook build →
  e2e smoke (`@angee/e2e`).
- Each stage is its own branch/commit (we're on `main`; branch per the workspace
  flow before editing).

---

## Stage 1 — Quick wins (batched, low risk)

Open lower-bound / in-range bumps with no hard blocker. Two independent batches.

### 1a. Python lock bumps
`uv lock --upgrade-package …` for:

| Package | Current → Latest | Note |
|---|---|---|
| django | 6.0.5 → 6.0.6 | patch (security/bugfix) |
| anthropic | 0.109.2 → 0.112.0 | minor |
| openai | 2.43.0 → 2.44.0 | patch |
| mcp | 1.27.2 → 1.28.1 | in fastmcp 2.x range (`<2.0`) |
| django-reversion | 6.2.0 → 6.3.0 | minor |
| django-simple-history | 3.11.0 → 3.12.0 | minor |
| django-environ | 0.13.0 → 0.14.0 | minor |
| cryptography | 48.0.0 → 49.0.0 | rolling major, not an API break |
| ruff (dev) | 0.15.15 → 0.15.20 | patch |
| pytest (dev) | 9.0.3 → 9.1.1 | minor |
| faker (dev) | 40.19.1 → 40.23.0 | minor |
| autobahn / txaio (transitive, dev via daphne) | 25.12.2 → 26.6.x | lock refresh only |
| graphql-core (transitive) | 3.2.8 → 3.2.11 | **opportunistic** — only if Strawberry fork's `requires-dist` allows; skip if it pins |

Gate: backend verification. `mcp` is the only one with any coupling (to
fastmcp 2.x) and it stays in range.

### 1b. JS lock bumps
Most are within existing caret ranges → `pnpm update -r` moves the lock. A few
cross the caret and need a range edit first.

In-range (just update lock): react/react-dom 19.2.6→.7, @types/react,
vitest, @tanstack/react-router, @tanstack/react-query, @codemirror/commands,
@codemirror/view, @codemirror/state, @graphql-codegen/cli (7.1.1→7.1.3, patch),
storybook + @storybook/* (10.4.1→10.4.6), tailwindcss + @tailwindcss/vite,
@vitejs/plugin-react, @xyflow/react, happy-dom, i18next, @tanstack/react-virtual,
vite (8.0.14→8.1.0), @base-ui/react (1.5→1.6 — smoke-test dialogs/popovers/menus),
@playwright/test (1.60→1.61).

Range edit then update:
- `@types/node` `^25` → `^26` (types-only; vite 8 peer satisfied).
- `lucide-react` `^1.17` → `^1.21` — **scan for renamed/removed icon names**
  across `@angee/ui` + the 7 addon packages that import it before bumping.

Gate: frontend verification.

---

## Stage 2 — Self-contained migrations (one branch each, targeted tests)

Each touches real code and needs its own verification; order is independent.

### 2a. @assistant-ui/react 0.12.28 → 0.14.24  (`@angee/agents`)
- Bump range to `^0.14.24` in `addons/angee/agents/web/package.json`.
- **Remove the `@assistant-ui/store` override** in `pnpm-workspace.yaml`
  (the `overrides:` block + its comment). 0.14.x ships `tap@^0.9.3` + `store@^0.2.19`,
  which realigns the version set the override was working around.
- Test the ACP side chat (the surface from the recent chat-UX commits e75f929a /
  039e9663): message render, streaming, typing indicator, slash commands,
  image/record attachments.

### 2b. streamdown 1.6.11 → 2.5.0  (`@angee/agents`)
- v2 swaps the render pipeline (`marked@^17`, `mermaid@^11`, remark/rehype).
  Peer `react@^18||^19` satisfied.
- Review the v1→v2 API diff; test agent markdown/message-stream rendering
  (code blocks, mermaid, links).

### 2c. @dagrejs/dagre 1.1.8 → 3.0.0  (`@angee/ui`)
- Two majors (1→2→3). No hard peer; the caret range is the only block.
- Review layout API changes across both majors; test `GraphView` node placement
  (the @xyflow/react graph view) before/after.

### 2d. fastmcp 2.14.7 → 3.x  (`addons/angee/mcp/`)
- Drop the `fastmcp>=2.14.7,<2.15` ceiling in `pyproject.toml` (line 38); this
  also frees `py-key-value-aio` 0.3.0 → 0.4.5 transitively.
- v3 restructured into `fastmcp-slim`. Audit the v3 changelog, then migrate the
  7 confirmed import sites:
  - `server.py:29` — `from fastmcp import FastMCP`
  - `graphql.py:32` — `from fastmcp.exceptions import ToolError`
  - `graphql.py:33` — `from fastmcp.tools.tool import Tool, ToolResult`
  - `middleware.py:15` — `from fastmcp.server.dependencies import get_access_token`
  - `middleware.py:16` — `from fastmcp.server.middleware import Middleware, MiddlewareContext`
  - `verifier.py:20` — `from fastmcp.server.auth import AccessToken, TokenVerifier`
  - (docstring `:func:`/`:class:` refs in those files + `server.py` mirror these paths)
- Test the MCP server: tool listing, a tool call end-to-end, the auth/verifier
  path (401 gating) and middleware. Relates to plan `fork-a6-mcp-authz-deferred.md`.

---

## Stage 3 — Upstream-blocked (decision required — NOT a bump)

These cannot be moved by editing our manifests; they wait on an upstream release
or an off-ramp from the blocking package. **Keystone:** `@refinedev/hasura@7.0.1`
(latest) blocks the entire GraphQL layer.

| Package | Now → Latest | Hard blocker |
|---|---|---|
| graphql | 16.14.0 → 17.0.1 | `@graphql-codegen/cli` & `client-preset` peer-cap `^16`; **and** `@refinedev/hasura@7.0.1` bundles `graphql@^15.6.1` |
| graphql-ws | 5.16.2 → 6.0.8 | `@refinedev/hasura@7.0.1` peer `graphql-ws@^5.9.1` (tightest single blocker) |
| pdfjs-dist | 5.4.296 → 6.0.227 | `react-pdf@10.4.1` pins `pdfjs-dist: 5.4.296` exactly in its own deps |

Decision for the architect:
- **graphql 17 / graphql-ws 6**: wait for `@refinedev/hasura` to ship newer peer
  ranges (and the codegen suite to add graphql 17), or evaluate migrating off
  `@refinedev/hasura`. The latter is a strategic question — refine/hasura is the
  single keystone gating the whole GraphQL stack. Owner: `docs/stack.md` Hasura row.
- **pdfjs-dist 6**: wait for `react-pdf` to ship a v6-compatible release; the
  exact pin (`5.4.296`, no caret) is correct to keep until then.

Recommendation: track upstream; do not force. Re-check each on a cadence.

---

## Stage 4 — Housekeeping (fold into the stages that touch each file)

- **Version drift:** normalize `@angee/storybook` `react: "^19.2.6"` → `"^19"`;
  tighten `@playwright/test` ranges to `^1.61`; raise stale `@storybook/*` lower
  bounds to match locked 10.4.x.
- **docs/stack.md:** the Hasura row reads `graphql-request 5 + graphql 15` —
  reconcile the wording now that the workspace runs graphql 16 (those are
  @refinedev/hasura's bundled versions; make that explicit). ACP row already
  updated.

---

## Suggested execution order
1. Stage 1a + 1b in parallel (two batches, two gates) — clears the long tail.
2. Stage 2a → 2b (both `@angee/agents`, share the chat verification surface).
3. Stage 2c, 2d independently.
4. Stage 3 — surface to the architect as a decision, not work.
5. Stage 4 folded into whichever stage edits the file.

---

## Execution Outcome (2026-06-26)

Done on `chore/dependency-upgrades`. **Gates green:** `pytest` 605 passed; JS
`pnpm typecheck` + `pnpm test` exit 0; `ruff check` clean; example `schema
--check` ok and `makemigrations --check` "No changes detected".

**Stage 1 — quick wins (shipped):**
- Python (`uv lock --upgrade-package …`): django 6.0.5→6.0.6, anthropic
  0.109.2→0.112.0, openai 2.43→2.44, mcp 1.27.2→1.28.1, django-reversion 6.2→6.3,
  django-simple-history 3.11→3.12, django-environ 0.13→0.14, cryptography 48→49,
  graphql-core 3.2.8→3.2.11 (fork allowed it), dev ruff/pytest/faker, transitive
  autobahn/txaio 25.12→26.6.
- JS (`pnpm update`): all in-range patch/minor (react/-dom 19.2.7, vitest 4.1.9,
  @tanstack/*, codemirror, storybook 10.4.6, tailwind, @vitejs/plugin-react,
  @xyflow/react, happy-dom, i18next, @base-ui 1.6, @playwright/test 1.61.1, vite
  8.1) + `@types/node` ^25→^26.

**Stage 2 — migrations (shipped, recipes adversarially verified by a workflow):**
- fastmcp 2.14.7→**3.4.2**: drop `<2.15` pin; one import `graphql.py`
  (`fastmcp.tools.tool`→`fastmcp.tools`); net dependency *reduction* (pydocket +
  redis chain dropped, py-key-value-aio 0.3→0.4.5). Authlib deprecation warning
  gone.
- @assistant-ui/react 0.12.28→**0.14.24** + removed the `@assistant-ui/store`
  override from `pnpm-workspace.yaml` (realigned tap 0.9.3 / store 0.2.19); no
  source changes.
- streamdown 1.6.11→**2.5.0** + added `@streamdown/code` (Shiki highlighting
  moved to an opt-in plugin); wired `plugins={{ code }}` and `lineNumbers={false}`
  (preserves the 1.x no-line-numbers look) in `AgentChat.tsx`.
- @dagrejs/dagre 1.1.8→**3.0.0** (graphlib 4.0.1); no source changes.
- lucide-react ^1.17→**^1.21** across 9 manifests; all imported icons still exist.

**Also fixed (pre-existing, requested):** `ScalarWidgets.stories.tsx` typecheck
error — the story now declares the relation widgets' real `unknown` value type
instead of inferring `string` from `initialValue`. Reproduced at clean HEAD, so
not introduced by the bumps.

**Stage 3 — NOT done (upstream-blocked, architect decision):** graphql 16→17,
graphql-ws 5→6, pdfjs-dist 5→6 — keystone blocker `@refinedev/hasura@7.0.1`
(+ `react-pdf@10.4.1` for pdfjs). Held intact in the lock; revisit when upstream
ships.

**Strawberry forks:** untouched, as intended.

**Note:** verified pending — runtime smoke (`angee dev`: agent chat render +
`/mcp` round-trip) not yet run; automated gates cover the logic. Changes are
uncommitted on the branch.
