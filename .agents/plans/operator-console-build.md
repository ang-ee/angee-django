# Operator Console — sliced build plan (Codex-built, reviewer-gated)

Build the `@angee/operator` console frontend at `src/angee/operator/web/`. The
backend bridge (the `operatorConnection` field + `OperatorDaemon` mint) is landed
(`operator-lift.md` Status). This plan **slices** the console into small,
independently buildable units. **Execution protocol:** each slice is built by
**Codex** (`codex:rescue`), then reviewed by **`react-reviewer` + `architecture-reviewer`**;
findings are fixed and the slice re-verified before the next slice starts.

> **Build-as-if-ready + TODO-for-testing.** The shared role-gating primitives
> (G1–G3 in `operator-lift.md`) and a reachable daemon do **not** exist yet. Build
> the console as if they will; where a seam can't be wired or tested, leave a
> precise `// TODO(<slice>):` and a safe fallback (ungated + server-enforced;
> fixtures; poll instead of SSE). Never block a slice on G1–G3 or a live daemon.

---

## Locked API facts (grounded — Codex must use these, not invent)

- **Manifest type = `BaseAddon`** (`@angee/base`), `export default`. A route is
  `BaseAddonRoute = AddonRoute & { component: ComponentType; title?; icon?;
  breadcrumbs? }` — `AddonRoute = { name; path; shell }`. **There is no `roles`
  field on a route yet** (G1 pending) → routes ship ungated; `// TODO(F2/G1)`.
  Console shell name = `"console"` (auth-gated by `createApp`'s `RequireAuth`).
- **Menu = `MenuItem` = `{ id; label?; to?; icon? }`** — **flat, no `parent` /
  `roles` / `sequence`** (G2 pending). Ship flat menu items; `// TODO(F2/G2)` for
  the nested rail group + role filtering. `composeAddons` claims route names /
  i18n keys / icons uniquely (collision = build error) — namespace everything
  `operator.*`.
- **Two transports:**
  - Django console client: `useSchemaClients()["console"]` (`@angee/sdk`) →
    a urql `Client`; run `operatorConnection { endpoint token }` against it
    directly (`client.query(doc, {}).toPromise()`), **not** `useAuthoredQuery`
    (which binds the app's default/public client).
  - Daemon client: a **second** urql `Client` (`createClient` from `urql`) at
    `connection.endpoint` with a `Bearer <token>` fetch; provide it to section
    bodies via urql's `Provider` (`import { Provider } from "urql"`). Sections use
    urql `useQuery` / `useMutation` → daemon client. **v1 polls** (re-execute on a
    2s tick); SSE/WS is `// TODO(F5)`.
- **Reuse `@angee/base` primitives** (all exported from `@angee/base`): `Card` /
  `CardHeader` / `CardTitle` / `CardContent`, `Badge`, `StatusIcon` / `StatusDot`,
  `CodeBlock` (polled log `<pre>`), `Spinner`, `Glyph`, `Table`, and the data
  views (`ListView` / `DataPage`) where a daemon list maps cleanly. **No new dep.**
- **i18n:** `i18n: { operator: { "<key>": "<msg>" } }` on the manifest; read with
  `useT("operator")` (`@angee/sdk`). Keys namespaced `menu.*`, `section.*`, etc.
- **Package:** mirror `examples/notes-angee/src/example/notes/web/` —
  `package.json` name `@angee/operator`, `exports { ".": "./src/index.ts" }`,
  deps `@angee/base`/`@angee/sdk` `workspace:*` + `urql`, peer
  `react`/`react-dom`/`@tanstack/react-router`, dev mirror notes; `tsconfig.json`
  extends `../../../../../tsconfig.base.json`. Add `src/angee/*/web` to
  `pnpm-workspace.yaml` packages (new shared glob — F0 owns it).
- **Codex verify scope = typecheck + vitest only.** `pnpm --filter @angee/operator
  typecheck` (and `vitest`). **Never run Storybook serve** (it hangs the sandbox
  — [[codex-hung-job-restart]]).
- **Claude (not Codex) runs `pnpm install`.** Codex's sandbox can't materialize the
  store / update the lockfile — it **stalls** on `pnpm install` (observed S0: files
  written, then hung ~7 min on install). So after a dep-adding slice, Claude runs
  `pnpm install`; Codex's typecheck only runs once the store is materialized. Tell
  Codex in the slice prompt: "do NOT run pnpm install; write files + report; Claude
  installs + typechecks."

---

## Slices (each: Codex build → react-reviewer + architecture-reviewer → fix → verify)

### S0 — Package skeleton + workspace glob
- `pnpm-workspace.yaml`: add `- "src/angee/*/web"`.
- `src/angee/operator/web/{package.json,tsconfig.json}` (mirror notes web).
- `src/angee/operator/web/src/index.ts`: minimal `BaseAddon` (`id:"operator"`,
  empty `routes`/`menus`) so the package typechecks in isolation.
- **Accept:** `pnpm install` resolves `@angee/operator`; `pnpm --filter
  @angee/operator typecheck` green.

### S1 — Daemon transport seam + data layer (`web/src/data/`)
- `types.ts` — daemon types: `OperatorConnectionInfo`, and per-section slices
  (`OperatorService`, `OperatorWorkspace`, `OperatorSource`, `OperatorJob`,
  `OperatorTemplate`, `OperatorSecret`, `GitOpsTopology`, `OperatorSnapshot`).
- `documents.ts` — `OPERATOR_CONNECTION_QUERY` (Django console) + daemon docs:
  one composite `SNAPSHOT_QUERY` (per-section `@include($want…)` flags) + the
  per-action mutations (service start/stop/restart/up; workspace create/destroy/
  sync-base; source fetch/pull/push; stack init/up/down/destroy + jobRun;
  secretSet/secretDelete). Inline strings (the documented two-transport exception).
- `operator-client.ts` — `createOperatorClient(connection)`: urql `createClient`
  with a Bearer-injecting `fetch`, `cacheExchange` + `fetchExchange`. SSE/WS
  forwarder = `// TODO(F5)`.
- `transport.tsx` — `OperatorTransportProvider`: fetch `operatorConnection` via
  `useSchemaClients()["console"]`; on `{endpoint,token}` build + memoize the daemon
  client and render urql `<Provider>`; render loading / "not configured" / error
  states otherwise. Hooks: `useOperatorClient()`, `useOperatorSnapshot(sections)`
  (urql `useQuery` + 2s poll), `useOperatorAction()` (urql `useMutation` wrapper).
- `fixtures.ts` — daemon-shaped fixtures for stories/tests.
- **Accept:** typecheck; hooks + provider typed; no stale-closure in the poll.

### S2 — Manifest + roles + nav (`web/src/{roles.ts,index.ts}`)
- `roles.ts` — `OPERATOR_ROLE_ADMIN="operator/role:operator_admin"`,
  `ANGEE_ROLE_ADMIN="angee/role:admin"`, `OPERATOR_ADMIN_ROLES` tuple.
- `index.ts` — 8 console routes (`/operator`, `/operator/services`,
  `/operator/workspaces`, `/operator/sources`, `/operator/gitops`,
  `/operator/operations`, `/operator/templates`, `/operator/secrets`), each
  `shell:"console"` + a `component` from `views/pages`; 8 flat `menus`; `i18n`
  bundle; `icons:{operator:…}` (a lucide/registry icon). `// TODO(F2/G1)`: route
  `roles`; `// TODO(F2/G2)`: nested rail group + `hasRole` nav filter — both land
  when the auth-roles primitives ship (server REBAC enforces meanwhile).
- **Accept:** typecheck; manifest composes (unique route names / i18n keys / icon).

### S3 — Section frame + pages + Overview + Services
- `views/OperatorSectionFrame.tsx` (wraps a section body in
  `OperatorTransportProvider`), `views/pages.tsx` (8 page components, one per
  section, each `<OperatorSectionFrame><XSection/></OperatorSectionFrame>`),
  `views/parts/StateTag.tsx` (daemon state string → base `Badge`/`StatusDot` tone).
- `views/sections/OverviewSection.tsx` (snapshot metric tiles + health),
  `views/sections/ServicesSection.tsx` (table + `StatusIcon` + start/stop/restart
  actions + polled `CodeBlock` logs).
- **Accept:** typecheck; both panes render against `fixtures` (story/test TODO).

### S4 — Workspaces + Sources + GitOps panes
- `views/sections/{WorkspacesSection,SourcesSection,GitOpsSection}.tsx` (+ any
  `parts/` and create panels). GitOps stays a styled lane layout — **no `@xyflow`**.
- **Accept:** typecheck; panes render against fixtures.

### S5 — Operations + Templates + Secrets panes
- `views/sections/{OperationsSection,TemplatesSection,SecretsSection}.tsx`
  (Operations: jobs + stack lifecycle; Templates: list + render inputs; Secrets:
  declared list + value reveal + set/delete — operator owns stack secrets).
- **Accept:** typecheck.

### S6 — i18n completion + tests + verify
- Complete `i18n` bundle (every `operator.*` key the panes reference).
- Vitest: snapshot `@include` slice selection; section row adapters; `StateTag`
  mapping; menu/manifest shape. (SSE-frame parser test only if F5 lands.)
- **Accept:** `pnpm --filter @angee/operator typecheck && vitest` green; full
  `pnpm -r typecheck` green (host unaffected).

### S7 — Live updates (poll → SSE/WS) — DEFERRED / TODO
- Replace the 2s poll with the daemon subscription transport once the daemon
  speaks it (`angee/docs/proposals/graphql-websocket-transport.md`). `// TODO(F5)`.

---

## Iteration protocol (per slice)

1. **Build (Claude):** Claude builds the slice directly against the Locked API facts
   + the real S1 types/documents, then runs `pnpm install` (only if new deps) +
   `pnpm --filter @angee/operator typecheck` → green. (The flip: codex builds stalled
   on `pnpm install`; Claude builds in-context faster and typechecks first try.)
2. **Review — codex plugin, FOREGROUND (`--wait`, NOT `--background`):**
   `node ".../codex-companion.mjs" adversarial-review --wait --scope working-tree
   "operator console slice <Sx>: review ONLY <the new files>; hooks/effects/urql,
   types, DRY, @angee/base, a11y; material ship-blockers only"`. The harness
   auto-backgrounds the long bash call but the alive process **pumps** the codex
   job to completion + notifies — `--background` jobs STALL (~5–7 min, no pump) and
   must be cancelled. Read the output file (or `result <job> --json`) for findings.
3. **Iterate:** fold the codex-review findings back to Codex (`codex:rescue`, or fix
   inline); re-verify. Loop until the review is clean and typecheck/vitest are green.
4. **Advance:** mark the slice ✅ in this file's ledger; start the next slice.

## Execution state (LIVE — resume from here after compaction/crash)

- **🔁 FLIP (per the operator):** **Claude builds each slice; Codex
  `adversarial-review`s it.** (Codex builds stalled on `pnpm install`; Claude has the
  `@angee/base`/transport API in context and builds faster + typechecks first try.)
- **⚙️ Review mechanism (LEARNED):** run codex `adversarial-review --wait`
  Run the review as a harness bg job (`run_in_background`) so node stays alive to pump
  the codex thread and the harness notifies on completion. The real stall cause was
  piping a running review through `head`/`tail`: SIGPIPE kills node and the thread
  detaches. Never pipe a live review; redirect to a file or read the job `.output`.
- **Ledger:** ✅ S0–S8 built + codex-reviewed. **Console CONNECTS live in `angee dev`**
  (admin → daemon, verified end-to-end: REBAC allow + minted scoped token).
- **Live-debug resolutions (this session):**
  - **Menu UX:** top bar now shows the *active app's* sections (framework fix in
    `base/chrome/TopMenu` + `menu-tree.appSectionItems`); operator sections render
    flat under `/operator`, gone from Notes. Rail = app switcher. 39 base tests green.
  - **Daemon wiring:** rendered `.angee/angee.yaml` + `templates/stacks/dev` run the
    `angee operator` daemon as a stack service on `${ports.operator}`; web+daemon share
    `${secret.operator-token}`; Vite proxies `/operator/graphql`. Daemon up on :9001,
    mints `aud=operator` tokens.
  - **Const-admin gate (model-less):** `operator/connection` admin reach via
    `relation admin: angee/role // rebac:const=admin` backed by a **`managed=False`**
    `OperatorConnection` anchor (no table; satisfies E009; forward check synthesises
    from schema). See [[synthetic-rebac-resource-const-admin]].
  - **Stale admin:** this stack's superuser wasn't in `angee/role:admin` →
    `rebac.roles.grant` (persists). Fresh stacks self-heal (resources load after sync).
  - **Cleanup:** table-less `operator.0001_initial` + `operator` added to
    `makemigrations` (rendered+template); `makemigrations --check` clean; removed the
    non-existent `rebac_roles sync` from the template.
- **S8 (done) — tests + i18n tidy:** added the vitest toolchain to `@angee/operator`
  (`vitest.config.ts` inlines `@angee/logo-react` so the chrome barrel's `.css` import
  is Vite-resolved, not Node-rejected; `package.json` test script + @testing-library/
  happy-dom devDeps). 3 suites, **14 tests green**: `run-action.test.ts` (boolean
  return + error surfacing + always-refetch — the fail-closed regression),
  `index.test.ts` (8 routes ↔ menu children ↔ console shell, unique names, icon/i18n),
  `StateTag.test.tsx` (slug → humanized label). i18n tidy: Overview count tiles now use
  `section.*.title`; dropped the dead `menu.*` keys so one used key family remains.
  `pnpm -r typecheck` clean. ⏳ Fold the `b4skiqkrt` findings when it reports.
  ⚠️ **Review hygiene:** never pipe a running codex review through `head`/`tail` —
  SIGPIPE kills the pumping node process and the codex thread detaches/stalls. Run it
  as a harness bg job (kept alive + notifies) or with full output.
- **S7 (done):** manifest = `index.ts` `defineAddon` (8 routes + "Operator" menu whose
  **`children` are the 8 sections** → framework `TopMenu` dropdown; `OperatorNav`/
  `SectionTabs` deleted — the hand-rolled double-nav UX fix) + `pages.tsx` + reverted
  frame. **Host wired** (notes `settings.py addons += angee.operator`, web `main.tsx`
  + dep, `pnpm-workspace src/angee/*/web`). **Framework fix** `base/settings.py`
  `_migration_modules` (model-less addons don't get a phantom migration namespace).
  **Dev stack** `templates/stacks/dev` + `vite.config.ts` (operator daemon as a
  service, `${secret.operator-token}`/`${ports.operator}`, `/operator/graphql` proxy).
  **Folded S7 findings:** narrowed the `/operator` proxy to `/operator/graphql`;
  documented the migrate heuristic + flagged the composer-side precise fix.
  `pnpm -r typecheck` + `migrate` clean; `angee dev` boots + serves the console.
- **⚑ Follow-up (framework):** `AngeeRuntime.render_sources` could emit an empty
  migration package for *every* composed addon (it has `self.addons`) so
  `_migration_modules` can map all labels — closes the empty/extension-only
  `models.py` edge case. Not needed for any current addon.
  **Host:** `examples/notes-angee/src/web/{package.json (+@angee/operator),
  src/main.tsx (addons:[…,operator])}` + `host/settings.py addons += "angee.operator"`.
  Verified: `pnpm install`, `pnpm -r typecheck` GREEN; `angee build`+`rebac sync`
  (clean)+`schema` → `operatorConnection` in console SDL.
- **Run:** `angee dev` from repo/workspace root → console at `/operator`. Login as the
  **superuser** (has `angee/role:admin` → passes the `operator/connection#read` gate).
  **Live data needs the daemon:** set `ANGEE_OPERATOR_URL` (daemon base) +
  `ANGEE_OPERATOR_TOKEN` (admin bearer) in the dev env; without them the field mints
  nothing → panes show "not configured" (proves wiring; not an error). Still codex-review
  S7 + S8 (i18n/tests) pending.
- **S3 (done):** [high] Services dropped lifecycle failures → `actionError` + catch +
  `ok===false`; [medium] inaccessible `<tr>` click → focusable name-cell `Button`.
- **S4 (done):** shared `views/parts/run-action.ts` `runDaemonAction` + `ServicesSection`
  refactored onto it + `WorkspacesSection` (sync-base/destroy + logs) + `SourcesSection`
  (fetch/pull/push + drift). **Folded S4 findings:** runDaemonAction now **fails closed**
  on a missing root payload (was silent success); `workspaceDestroy` gated behind a
  confirm (TODO: styled AlertDialog). Typecheck green. (GitOps moved to S5.)
- **S5 (done):** `GitOpsSection` (read-only lanes/edges, no `@xyflow`) +
  `OperationsSection` (stack lifecycle + jobs). **Folded 4 findings:** `window.confirm`
  → styled **`useConfirm`** (`@angee/base`, danger, names the resource) for
  workspace-destroy + stack-destroy; GitOps edge `state` rendered; job expansion keyed
  on `id` (not `name`); Run disabled while a job is `running`. Typecheck green.
- **S6 (done):** `TemplatesSection` (read-only catalog + input schema) + `SecretsSection`
  (scoped set-form + declared list w/ present/required + delete via `useConfirm`).
  **Folded 2 findings:** `scope` now threaded through set *and* delete (scoped secrets
  target correctly); `runDaemonAction` returns a success boolean so the set-form value is
  preserved on failure. Typecheck green.
- **Next — S7 (Claude builds) = manifest+pages wiring:** `views/pages.tsx` (8 page comps,
  each `<OperatorSectionFrame><XSection/></OperatorSectionFrame>`) + `index.ts`
  `defineAddon` (8 console routes + flat menus + `operator` icon registered + i18n bundle;
  `// TODO(G1/G2)` route `roles`/nav role-filter). Then S8 = i18n key completeness pass +
  vitest (run-action success/fail + scoped-secret targeting regression) + final
  `pnpm -r typecheck`.
  Then i18n completion + vitest + final `pnpm -r typecheck`.
- **Reliability note:** codex jobs in this env **stall ~7 min in** (S0 build stalled on
  install; S0 review stalled on context `rg`). Protocol: each loop tick, if a job shows
  no log movement for many minutes, `cancel` it and salvage (Claude finishes the verify /
  trivial fixes), then proceed. Don't wait the full job out.
- **Loop:** CronCreate job `2fea980c` (`*/15 * * * *`). Cancel with CronDelete.
  Companion subcommands: `status [id|--all] --json`, `result <id> --json`,
  `cancel <id> --json`, `task`, `review`, `adversarial-review` (focus text only on
  the adversarial variant).
- **Review done this slice?** ❌ not yet (waiting on Codex build).
- **Loop cadence:** `/loop 15m` — each tick: (1) is the Codex build done
  (`status <job>`)? (2) if done & review not run → kick the **codex plugin**
  `adversarial-review --background --scope working-tree` on the slice; (3) fold the
  codex-review findings (back to Codex via `codex:rescue` or fix inline) until clean +
  `pnpm --filter @angee/operator typecheck` green; (4) mark slice ✅, delegate the
  **next** slice to Codex; (5) update this block.
- **Next slice to delegate (when S0 ✅):** **S1** (daemon transport seam + data layer,
  `web/src/data/`). Delegate verbatim from the S1 spec; Codex verify = typecheck/vitest
  only, never Storybook serve ([[codex-hung-job-restart]]).
- **Reviews = codex plugin** (`codex-companion.mjs adversarial-review` / `review`),
  NOT the Claude reviewer agents — cross-model adversarial pass on each slice diff.

## Host wiring (after S6, optional — needs a daemon)
Mount `operator` in `examples/notes-angee/src/web/src/main.tsx` (`addons:[…,
operator]`) **and** compose `angee.operator` in the notes Django host, only when
`angee dev` runs a daemon. Until then the console is storybook/fixtures-verified.
