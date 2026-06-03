# Operator Addon Lift — backend bridge + console UI

Reconstruct the **operator** capability into this repo as a base addon at
`src/angee/operator/`: a thin Django bridge to the local Angee operator daemon
(one connection field + one REBAC gate) plus an admin console UI
(`@angee/operator`) whose section panes talk to the daemon directly over a
second GraphQL transport.

Feature reference (read-only, intent only — **never** a source of files):
`../angee-django-p1/addons/operator`. Paths below are feature references, not
sources. **Lift hygiene:** reconstruct against this repo's idioms; no produced
file byte-identical to the reference; **no provenance anywhere** in shipped code,
docs, resource data, filenames, or commit messages (no "lifted/ported", no prior
repo, no plan numbers, no daemon-repo internals exposed as ours).

---

## Status

- ✅ **Backend bridge (B1–B5) + D-MINT swap landed** —
  `src/angee/operator/{__init__,apps,daemon,schema}.py` + `permissions.zed`;
  `tests/test_operator.py` (16 tests). Endpoint resolution + minting are grouped on
  the **`OperatorDaemon`** class (`daemon.py`), not loose functions. The field
  **mints** a short-lived, scoped `aud=operator` token server-side via the daemon's
  `POST /tokens/mint` (admin bearer stays in Django) and serves that — the daemon's
  two-tier `s.auth` + `Verify` accept it. Gates green: ruff, mypy (4 files), full
  suite (117). Composition smoke-tested (reversibly opting operator into the notes
  host): `rebac sync` merges `operator`'s zed with no drift; the console SDL emits
  `operatorConnection: OperatorConnectionInfo` (nullable), console-only. Operator
  stays **opt-in** (not committed into any host — D-LOAD).
- ⬜ **Frontend console (F0–F7)** — pending the shared auth-roles primitives
  (G1–G3) and the daemon transport/verifier.

## 0. The capability and where it lands

**What operator is.** A *bridge*, not a state owner. The daemon (sibling Go
service) owns all lifecycle + state (stack, services, jobs, sources, workspaces,
templates, secrets, GitOps). Angee ships exactly three things:

| Concern | Owner |
|---|---|
| Daemon state (read/write) | Daemon — the browser talks to it directly over GraphQL |
| `operatorConnection` field (`{endpoint, token}`) | Django operator addon |
| `operator_admin` role gating that field + console | Django operator addon (REBAC) |
| Console UI (`/operator/*`) | `@angee/operator` — addon-owned, mounts under `console` shell |

**Level — base addon.** Operator is a platform capability inherited by every
project that wants to drive the local stack; it is not product logic. It lands
beside `iam`/`resources`, **not** in a consumer addon.

- **Backend** → `src/angee/operator/` (`apps.py`, `daemon.py`, `permissions.zed`,
  `schema.py`; no `models.py` — the addon holds no Django state).
- **Frontend** → `src/angee/operator/web/` (package `@angee/operator`, label =
  directory, scoped like `@angee/base`/`@angee/sdk`). **New shared infra:** no
  base addon ships a co-located `web/` today (`src/angee/iam/` has none), and
  `pnpm-workspace.yaml` has **no** `src/angee/*/web` glob. The `iam-roles-lift.md`
  D6 only *proposes* `iam/web`; it is not landed. So introducing the
  `src/angee/*/web` glob is **new work co-owned with that D6** — whichever lift
  lands first owns it, the other reuses it (§1, F0). Do not treat it as existing
  precedent.

**Decision points for the architect (see §6):** frontend placement
(`src/angee/operator/web` vs a top-level `packages/operator`); whether v1 builds
the full console now or backend-first; and whether the daemon is reachable in
this dev stack for end-to-end verification.

---

## 1. Locked constraints (grounded in this repo)

- **Hand-written GraphQL, the `schemas` dict.** A base addon contributes by
  exporting `schemas = {"<bucket>": {"query": [...], "types": [...]}}` from
  `schema.py` (`iam/schema.py:102`), merged by `base/graphql/schema.py`. There is
  **no** `GraphQLContribution`/`QueryField` machinery here — drop it. Operator is
  admin-only, so it lands in the **`console`** bucket **only** (not `public`).
- **REBAC checks via `check_field_access` + `ObjectRef`.** The gate is
  `check_field_access(backend(), subject=<actor>, action="read",
  resource=ObjectRef("operator/connection", "default"))` (`base/access.py:43`).
  `ObjectRef` takes a bare `type:id`, so a **synthetic** resource (no Django row)
  works exactly like a model resource. There is **no** `check_permission(info,
  resource_type=…, resource_id=…)` helper here — drop it. The current actor is
  `current_actor()` (`from rebac import current_actor`), which already returns a
  `SubjectRef | None` — the settled local idiom at
  `base/graphql/subscriptions.py:33`. No conversion, no per-resolver guessing.
- **Settings via `settings_defaults` + `getattr(settings, …)`.** The addon
  default base URL is a `settings_defaults` entry on the AppConfig
  (`iam/apps.py:15`); resolution + minting live on an `OperatorDaemon` class
  (`daemon.py`). Reading the **token**
  from the process env is legitimate (the dev supervisor injects it) — keep a
  narrow env read for the secret, default everything else through settings. **No**
  `AngeeAppConfig` keys (`kind`/`namespace`/`compose_emits_runtime`/
  `framework_managed`/`sqid_prefix_namespace`/`assets` dict) — those do not exist
  on this repo's `BaseAddonConfig`.
- **REBAC schema = const-backed admin idiom.** `permissions.zed` carries the
  `// @rebac_package: operator` header (`iam/permissions.zed:1`). Admin reach is
  the const-backed relation `admin: angee/role // rebac:const=admin`, **not** a
  literal `angee/role:admin#member` in every relation and **not** an install-tier
  tuple ([[const-backed-relations]]). Fail-closed posture
  (`REBAC_SUPERUSER_BYPASS=False`) is assumed.
- **Reuse the stack; add no deps for v1.** urql, React, i18next, and the
  `@angee/base` primitives already cover the console (tables, badges, log `<pre>`,
  section nav). v1 introduces **no new frontend or backend dependency** — if one
  surfaces it carries a `docs/stack.md` owner row in the same change (flag, don't
  add silently).
- **Per-area gate green before commit; run from root** (`angee build` →
  `makemigrations` (none expected — no models) → `migrate` → `rebac sync` →
  `schema --check`; frontend `pnpm -r typecheck && pnpm -r test`).

### Framework gaps to fill FIRST (owner = `base`/`sdk`, shared with auth-roles)

The console is **route- and nav-role-gated**, and that gating does not exist yet.
It is **not** operator-owned — it is the same primitive the auth/roles frontend
needs (`notes-auth-lift.md` C5/C6, `iam-roles-lift.md` F4/D7). Operator must
**consume** it, never reinvent it.

- **G1 — `roles` on a route + `RoleGate`.** `AddonRoute`/`BaseAddon` route gains
  an optional `roles` field; an unmet role renders the framework forbidden state
  **inside** the shell. Today routes are `{name, path, shell, component}` with no
  `roles` (`packages/sdk/src/define-addon.ts`). Owner = `sdk` + `base/createApp`.
- **G2 — menu role-filtering + nested rail group.** `ChromeMenuItem` already
  nests (`parent`/`children`/`group`, `menu-tree.ts`) but is **not** filtered by
  role and has no `roles`/ordering field. Add role-filtering driven by
  `hasRole(...)` and a stable ordering key. Owner = `base/chrome`.
- **G3 — real `hasRole`.** The SDK already derives `hasRole` from `user.roles`
  (`packages/sdk/src/auth.ts:97`); the gap is `currentUserToAuthState` returning
  `roles: []` / `hasRole: () => false` (`auth.ts:87`). The iam-roles lift adds the
  backend `currentUser.roleRefs` (D7 UX-hint) on `UserType` and maps it →
  `AuthUser.roles` in the SDK (its C5). **Operator consumes `useAuth().hasRole`
  only** — it touches neither the backend field nor the SDK auth shape. Owner =
  `iam` backend + `sdk`, **not** operator.

If G1–G3 are not landing in this lift, the console ships **ungated** (server-side
REBAC still enforces; the field returns `null` to non-admins so panes stay empty)
— acceptable only as an explicit interim (see §6 D-SCOPE).

---

## 2. Backend phases (the bridge — small, self-contained, landable now)

Feature references: `addons/operator/angee/operator/{graphql,conf,apps}.py`,
`addons/operator/rebac.zed`.

### B1 — `src/angee/operator/apps.py`
`OperatorConfig(BaseAddonConfig)`: `name="angee.operator"`, `label="operator"`,
`depends_on=("base", "iam")` (the gate references `auth/*` + `angee/role`),
`default=True` (Django's `AppConfig.default` = "auto-select this config when the
app is listed"; `iam` sets it too — it is **not** an Angee opt-in toggle).
`rebac_schema` defaults to `"permissions.zed"`. `settings_defaults` carries the
daemon base-URL default (D-DEFAULTBASE below). No `models.py`, no `ready()`
signals. **Opt-in is host composition:** a project includes operator by listing
`angee.operator` in its `addons=(...)` (`host/settings.py`); no host lists it by
default, so operator stays off until a daemon-bearing host opts in (D-LOAD, F7).

### B2 — `src/angee/operator/permissions.zed` (const-backed)
```
// @rebac_package: operator
definition operator/role {
    relation member: auth/user | auth/group#member
}

definition operator/connection {
    relation admin: angee/role // rebac:const=admin
    relation reader: operator/role:operator_admin#member
    permission read = reader + admin->member
}
```
- Platform admins (superuser → `angee/role:admin`, synced by `iam/signals.py`)
  read the connection through `admin->member` — **no seed tuple needed**.
- `operator_admin` is a role id the schema references; granting it to a
  *non-admin* actor is a `rebac.roles.grant(actor, "operator/role:operator_admin")`
  call. v1 needs **no install seed** (const-backing covers admins); an optional
  demo grant for a non-admin principal can ride a seed callable **iff** the demo
  wants a non-superuser operator (defer — see §6).
- The `resources` loader does **not** write `rebac.Relationship` rows
  (`iam-roles-lift.md` B4: `rebac sync` syncs schema only; the loader is a model
  `ModelResource`). Const-backing sidesteps this for admins (no seed at all); a
  non-admin `operator_admin` grant uses an idempotent `rebac.roles.grant` seed
  callable, exactly as iam-roles B4 prescribes (D-NONADMIN is only the scope
  question — does the demo want a non-superuser operator — not a mechanism
  unknown).

### B3 — `src/angee/operator/daemon.py` (`OperatorDaemon`: endpoint + admin bearer + mint)
Small, owns one concern: resolve the **browser-visible GraphQL URL** and the
**admin bearer** (server-side only — it is never served to the browser; it is the
credential Django uses to *mint* a scoped token, B4):
- **Endpoint:** `ANGEE_OPERATOR_GRAPHQL_ENDPOINT` (full URL wins) → `ANGEE_OPERATOR_URL`
  (base) → `settings_defaults` base (default `/operator`). Append `/graphql` once;
  return the full URL the browser uses verbatim (default `/operator/graphql` keeps
  same-origin reverse-proxy deployments CORS-free).
- **Admin bearer:** `getattr(settings, "ANGEE_OPERATOR_TOKEN")` → `os.environ`
  secret key. Stays **server-side**. Keep the env read narrow (the dev supervisor
  sets it).
- Drop `exceptions.py` (`OperatorNotConfiguredError`) — the resolver returns
  `null`, never raises. **Note:** unlike the reference, v1 *does* make one
  server→daemon call — the mint (B4) — so a tiny outbound POST helper is needed
  (stdlib `urllib`, matching `resources/fetch.py`; **no new dep** — do not pull
  `httpx`).

### B4 — `src/angee/operator/schema.py` (the connection field)
- `@strawberry.type OperatorConnectionInfo { endpoint: str; token: str }`.
- `@strawberry.type OperatorQuery` with `@strawberry.field operator_connection(info)
  -> OperatorConnectionInfo | None`:
  1. `actor = current_actor()` (cite `base/graphql/subscriptions.py:33`); if
     `actor is None` → `None`.
  2. `check_field_access(backend(), subject=actor, action="read",
     resource=ObjectRef("operator/connection", "default"))`; if denied → `None`.
  3. **Mint a scoped token server-side:** POST the daemon's `/tokens/mint`
     (`mintConnectionToken(actor, scope, ttl)`) authenticated with the admin
     bearer from B3, requesting `aud=operator` + the scope the actor is approved
     for. Return `OperatorConnectionInfo(endpoint=…, token=<minted token>)`. On no
     admin bearer / mint failure → log one INFO line per process and return
     `None`. **The admin bearer never reaches the browser** — the browser holds
     only the short-lived, scoped token.
- `schemas = {"console": {"query": [OperatorQuery], "types": [OperatorConnectionInfo]}}`.
  **Console bucket only** (admin surface).
- `null` is the single "no access" shape (not an error) so the browser consumer
  stays trivial.
- **Daemon dependency (D-MINT):** a minted token is only *accepted* by the daemon
  once it lands the shared `verifyToken` + two-tier `/graphql` auth
  (`angee/docs/proposals/edge-ingress-caddy.md` Part 2 + the WS transport
  proposal's `InitFunc`). Until then the daemon accepts only the admin bearer, so
  either (a) sequence this field behind that daemon work, or (b) ship a clearly
  marked **dev-only interim** that serves the admin bearer — never the default.

### B5 — Backend verify
`angee build` → `migrate` (no new migrations expected) → `rebac sync` →
`schema --check` (console SDL now carries `operatorConnection` +
`OperatorConnectionInfo`). pytest: field returns `null` for an anonymous actor;
`null` for a logged-in non-admin; `{endpoint, token}` for a platform admin **with**
a token set; `null` for an admin with **no** token (and the INFO line fires once);
endpoint resolution chain (full-URL setting wins; base-URL appends `/graphql`
once; default base). `django-reviewer` on the slice.

---

## 3. Frontend phases (the console — depends on §1 G1–G3)

Package `@angee/operator` at `src/angee/operator/web/`. Reconstruct the
section-routed console: one `console` route per section gathered under a
role-gated rail menu group; each section's data comes from the daemon over a
**second urql transport** opened per-connection.

Feature references: `addons/operator/ui/base/src/` (manifest, `views/data/`,
`views/sections/*`, `views/parts/StateTag`, `views/panels/*`).

### F0 — workspace + package skeleton
- **Introduce** the `src/angee/*/web` glob in `pnpm-workspace.yaml` packages —
  new shared infra (no base addon has a `web/` today), co-owned with the
  iam-roles D6; whichever lift lands first adds it.
- `package.json` `@angee/operator`, `exports` `./src/index.ts`, peer deps
  `@angee/base`, `@angee/sdk`, `react`, `react-dom`, `urql` (all `workspace:*`/
  locked — **no new deps**). `tsconfig.json` matching the notes web package.

### F1 — the two-transport seam (`web/src/data/`)
The Django client (already provided by `@angee/sdk`, reachable via
`useSchemaClients()`) fetches `operatorConnection { endpoint, token }` from the
**console** schema. On success the addon opens a **second** urql client pointed at
`endpoint` with `Authorization: Bearer <token>`:
- **Reuse** `createUrqlClient` (`packages/sdk/src/graphql-client.ts`) verbatim for
  the HTTP query/mutation path — just a different `url` + a Bearer-injecting
  `fetch`. **No bespoke transport in v1:** the panes **poll** the snapshot query on
  a 2s tick (plain on-stack `fetch`), so v1 needs **no** subscription forwarder at
  all and carries no cross-repo dependency (D-SSE).
- **Do NOT hand-roll an SSE forwarder.** Live updates (F5) ride the **locked
  `graphql-ws` stack row** once the daemon speaks it: the daemon is gqlgen
  (`transport.Websocket{}` is built in; `gorilla/websocket` already a dep; it
  registers only SSE+POST today), so the browser client reuses the SDK's
  `createGraphQLWSSubscriptionForwarder` and **zero** custom transport code lives
  in operator. A hand-rolled SSE forwarder is off-stack **and** bespoke **and**
  duplicates a transport the SDK already owns — avoid it (D-SSE).
- A `OperatorTransportProvider` (React context) supplies the daemon client; each
  route body is wrapped in it. The enabler: a route `component` is an opaque
  `ComponentType` that `createApp` renders **inside** its fixed provider stack
  (`createApp.tsx`), so a route body can wrap itself in any additional context —
  addons need not (and cannot) inject into `createApp`'s stack. Hooks
  `useOperatorClient()` / `useOperatorSnapshot(section)` / `useOperatorAction()`
  consume it.
- Daemon GraphQL documents live in `web/src/data/` as inline query strings (one
  composite snapshot query with per-section `@include` flags + per-action
  mutations). This is the documented exception to op-name binding: there is no
  Django catalog model to bind these by name.

### F2 — manifest, routes, nav (`web/src/index.ts`)
`defineAddon({ id:"operator", routes:[…], menus:[…], i18n:enOperatorBundle })`:
- **8 console routes** (`/operator`, `/operator/services`, `/operator/workspaces`,
  `/operator/sources`, `/operator/gitops`, `/operator/operations`,
  `/operator/templates`, `/operator/secrets`), each `shell:"console"`,
  `roles: OPERATOR_ADMIN_ROLES` (G1).
- **Nested menu group:** a parent `ChromeMenuItem` `id:"operator"` (likely
  `group:"platform"`) with one child per section via `parent:"operator"` +
  `children` (`menu-tree.ts`), role-filtered via G2, stable ordering. Note
  `ChromeMenuGroup` is a **closed** union (`"domain" | "platform"`) — do **not**
  invent `group:"operator"`; the nesting axis is `parent`/`children`, not `group`.
  If a new top-level grouping axis is genuinely wanted, that is a `base/chrome`
  union change called out under G2 with its owner.
- `roles.ts` exports `OPERATOR_ROLE_ADMIN` / `ANGEE_ROLE_ADMIN` /
  `OPERATOR_ADMIN_ROLES` (the ref strings used by route + group gating).

### F3 — sections (`web/src/views/sections/*`), reusing `@angee/base`
Eight panes, each reading its daemon slice and reusing base primitives rather
than rebuilding:

| Pane | Daemon ops (read / act) | Base primitives reused |
|---|---|---|
| Overview | `health`, `stackStatus`, `sources`, `stackLogs` (composite snapshot) + metric tiles | metric tiles, `Badge`/`StatusDot` |
| Services | `stackStatus.services`, `serviceLogs`; `serviceStart/Stop/Restart`, `serviceUp` | `ListView`-style table, `StatusIcon`, `CodeBlock` (logs) |
| Workspaces | `stackStatus.workspaces`, `workspaceStatus/Logs`; `workspaceCreate/Destroy/SyncBase` | table, create panel (form), `CodeBlock` |
| Sources | `sources`; `sourceFetch/Pull/Push` | table, `StatusDot` |
| GitOps | `gitOpsTopology(withCommits)`; per-slot fetch/pull/push/merge/rebase/abort/publish | **lane layout as styled nodes** (no `@xyflow` in v1) |
| Operations | `stackStatus.jobs`; `stackInit/Up/Down/Destroy`, `jobRun` | table, `CodeBlock` |
| Templates | `templates`, `template(ref)` (list + render via `workspaceCreate`) | table + input fields |
| Secrets | `secrets`, `secret(name)`, `secretValue(name)`; `secretSet/Delete` | table + reveal control (operator owns stack secrets) |

A small `StateTag` part maps daemon state strings → base `Badge`/`StatusDot`
tones. Logs render as polled **plain `CodeBlock` (`<pre>`)** on a 2s tick while a
panel is open — `CodeBlock` is already shipped, **no new dep**. ANSI coloring is
**out of v1**: `ansi-to-react` has a `docs/stack.md` owner row but is **not in
the lockfile**, so enabling it is a real `pnpm add` (flag it; do not describe it
as free reuse). **Explicitly out of v1:** ANSI logs, `@xyflow` DAG, diff viewer,
`@xterm` terminal, Django catalog tabs (no models).

### F4 — i18n (`web/src/i18n/en.ts`)
`enOperatorBundle` under the `operator` namespace (`useT("operator")`, merged by
`createApp`'s `mergeI18n`). Menu labels + section copy keyed; conventions match
the base/notes bundles.

### F5 — live updates (fast-follow, paired with the daemon) — NOT v1
v1 ships **poll-only** (F1's 2s tick) — no subscription transport. Live updates
(`onGitOpsTopologyChange`, `onWorkspaceStatusChange`, `onServiceLogs`,
`onWorkspaceLogs`) are a fast-follow that **pairs with a daemon-side change**:
register gqlgen's `transport.Websocket{}` and move bearer auth into the graphql-ws
`connectionParams` init (the browser can't set a WS `Authorization` header), which
also means restructuring the daemon's POST-only handler wrapper
(`internal/operator/graphql.go:41`) to let the `GET` upgrade through. Once the
daemon speaks graphql-ws, the operator client reuses the SDK's
`createGraphQLWSSubscriptionForwarder` with no new operator code. **Cross-repo +
auth-boundary work** — schedule deliberately, do not bundle into the UI lift.

### F6 — Frontend verify
`pnpm -r typecheck && pnpm -r test`. Storybook + `web/src/data/fixtures.ts`
(daemon-shaped fixtures) verify every pane **without a live daemon**. Tests cover:
the SSE frame parser; the snapshot `@include` slice selection; section row
adapters; menu/role gating (admin sees the rail group, a non-admin does not);
i18n bundle completeness. `react-reviewer` + `architecture-reviewer` on the slice.

### F7 — host wiring (optional, host-level)
Mounting the console in a host (`examples/notes-angee/src/web/src/main.tsx`
composes `[notes, authAddon]`) makes it visible under `angee dev`. Add `operator`
to that list **iff** the dev stack runs a daemon to talk to; otherwise leave it
storybook-only. This is a host composition choice, not part of the addon.

---

## 4. What is reused / dropped / flagged

**Reused (not ported):** `schemas` dict + `@strawberry.type`; `check_field_access`
+ `ObjectRef` + `backend()` + actor surface; `BaseAddonConfig` + `settings_defaults`
+ `rebac_schema`; const-backed admin idiom; `createUrqlClient` shape;
`defineAddon`/`BaseAddon` routes + `ChromeMenuItem` nesting; `@angee/base`
`ListView`/`Badge`/`StatusIcon`/`StatusDot`/`CodeBlock`/`SectionNav`/`SectionTabs`;
`useT`/`I18nResources`; icon registry; `useSchemaClients()` for the Django client.

**Dropped / simplified:** the `GraphQLContribution`/`QueryField` declarative layer;
the `check_permission(info, …)` helper; `operator_server_graphql_endpoint`
(no server→daemon calls in v1); `exceptions.py` (resolver returns null);
install-tier role tuples (const-backing replaces the admin bridge); the
`AngeeAppConfig` keys not on `BaseAddonConfig`; `@xyflow`/diff/`@xterm`/catalog
tabs (deferred).

**Flagged for the architect:**
- **G1–G3 are shared auth-roles primitives**, not operator's. Sequencing depends
  on whether they land in this lift or are reused from the auth-roles lift.
- **`docs/stack.md` owner rows / installs:** v1 adds **no** dep. The B4 mint call
  is one outbound JSON POST — use stdlib `urllib` (matching `resources/fetch.py`),
  **not** `httpx` (which is not in the stack at all). Two future items are real
  installs: **ANSI logs** (`ansi-to-react` has an owner row but is absent from the
  lockfile — a `pnpm add`), and a richer Django→daemon **outbound HTTP** client
  (catalog ingest / secret push) if one is ever wanted — that would carry an owner
  row. Do not add silently.
- **Daemon dependency (D-MINT):** serving a minted scoped token requires the
  daemon to verify it (`edge-ingress-caddy.md` Part 2 + the WS `InitFunc`). The
  Django field is correct to write now (mint + serve); it only *works end-to-end*
  once the daemon accepts minted tokens.
- **Add the `operator` icon** to `base`'s icon registry
  (`packages/base/src/chrome/icon-registry.ts`) — the manifest references
  `icon:"operator"`.
- **Daemon availability** gates end-to-end verification (§6 D-DAEMON).

---

## 5. Sequencing & gates

1. **Backend B1–B5** is self-contained and unit-verifiable **now** with the mint
   call mocked (gate logic returns `null`/`{endpoint, token}` deterministically);
   real end-to-end minting depends on the daemon verifier (D-MINT). One commit.
   `django-reviewer`.
2. **Framework gaps G1–G3** (owner = base/sdk/iam) — land with, or reuse from, the
   auth-roles frontend lift. The console **blocks** on these (or ships ungated as
   the §6 D-SCOPE interim).
3. **Frontend F0–F6** after G1–G3. F0/F1 (skeleton + transport) → F2 (manifest) →
   F3/F4 (sections + i18n) → F5 (SSE) → F6 verify. `react-reviewer` +
   `architecture-reviewer`.
4. **F7 host wiring** last, and only with a reachable daemon.

**Per-phase gate (green before commit):**
```sh
# backend
uv run ruff check . && uv run mypy src/ && uv run pytest
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py rebac sync
uv run examples/notes-angee/manage.py schema && uv run examples/notes-angee/manage.py schema --check
# frontend
pnpm -r typecheck && pnpm -r test && pnpm run build
```

---

## 6. Open decisions (architect call)

- **D-SCOPE — v1 reach.** (a) **Backend bridge now**, console after the shared
  auth-roles primitives (G1–G3) land — smallest, fully verifiable today
  *(recommended)*; (b) backend + full console + build G1–G3 here — one larger
  push, duplicates sequencing the auth-roles lift already owns; (c) backend + a
  **deliberately ungated** console as an interim (server REBAC still enforces).
- **D-PLACE — frontend home.** `src/angee/operator/web/` (`@angee/operator`,
  co-located with the backend addon; **introduces** the new `src/angee/*/web`
  glob, co-owned with iam-roles D6) *(recommended)* vs a top-level
  `packages/operator` (no glob change, but separates an optional addon's UI from
  its backend and treats it like core). Note: co-location is the right *shape*,
  but it depends on landing the glob — it is not yet an existing pattern.
- **D-DAEMON — verification path.** Is the operator daemon reachable in this
  workspace's `angee dev`? If not, v1 console is **storybook + fixtures** verified
  (F6) and F7 host wiring waits.
- **D-SSE — subscription transport.** *(Resolved by investigation.)* The daemon
  is gqlgen with `gorilla/websocket` already in tree but registers only SSE+POST
  (`internal/operator/graphql.go:33`). **v1 polls** (no transport, no cross-repo
  dependency); **live updates teach the daemon `transport.Websocket{}`** so the
  browser reuses the SDK's locked `graphql-ws` forwarder (F5) — a paired,
  auth-touching Go change. **Do not** hand-roll an SSE forwarder (off-stack +
  bespoke + duplicates the SDK). Only if the daemon commits to SSE permanently:
  add the `graphql-sse` exchange + a `docs/stack.md` owner row — still not a
  hand-roll.
- **D-MINT — minted scoped token vs admin bearer.** ✅ **Daemon side landed** —
  `POST /tokens/mint {actor, scope[], ttl}` → `{token, ...}` and a two-tier
  `s.auth` (`Verify` accepts the minted `aud=operator` token; scope carried, not
  yet enforced per-resolver). **Swap is buildable now:** the field mints a
  short-lived token server-side over the admin bearer (which stays in Django) and
  serves that — the browser never holds a root credential. Open contract choices
  (Django side): the `actor` string = the REBAC subject ref of `current_actor()`;
  a **server-reachable** mint base (absolute `ANGEE_OPERATOR_URL`, distinct from
  the browser endpoint); `scope` + `ttl` as **settings-overridable** seams
  (default empty scope = full today, set when the daemon's scope map lands).
- **D-DEFAULTBASE — default base URL.** Confirm `/operator` (same-origin
  reverse-proxy, CORS-free) is the right `settings_defaults` default for this
  repo's dev topology.
- **D-LOAD — opt-in via host composition.** Operator opt-in is **not** an
  `AppConfig.default` toggle (that is Django's auto-select attribute) — it is
  whether a host lists `angee.operator` in `addons=(...)`. No host lists it by
  default, so it stays off until a daemon-bearing host opts in. Decide which host
  (if any) composes it once the daemon is in the dev stack.
- **D-NONADMIN — non-admin operator access.** v1 needs no install seed
  (const-backing covers admins). Decide if the demo wants a *non-superuser*
  `operator_admin`; if so, add an idempotent `rebac.roles.grant` seed callable
  (pending the §B2 `Relationship`-writability check).
