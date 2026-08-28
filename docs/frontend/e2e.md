# End-to-End Testing

End-to-end (e2e) tests drive the **real, composed product** — the Vite/React SPA
talking to the running Django + GraphQL backend — and assert what a user (human
or agent) actually sees. They are the top of the testing pyramid: slow, few, and
high-signal. Unit and integration coverage lives elsewhere (pytest for the
backend, Vitest for the frontend); this page owns the browser layer.

[The opinionated stack](/guide/stack) locks **Playwright** as the browser engine.
This page owns *how Angee uses it* and what the framework ships so a consumer
addon gets e2e for free.

## The owning idea: a workspace is the test environment

Angee already owns environment isolation. The seeded framework-dev stack
provides the environment: bringing it up runs the full deterministic bootstrap
(`provision` = build → migrations → rebac sync → resources load install,demo →
schema), which **seeds the demo data** the tests assert against. (The retired
`workspaces/dev` template used to chain an isolated inner stack per topic
workspace; a successor for per-branch inner stacks is planned — until then the
suite runs against the live stack's allocated UI port.)

So the e2e harness does **not** reinvent test databases, fixtures, login servers,
or per-run isolation. It inherits them:

| Concern | Owner | The harness does |
|---|---|---|
| Unique database per run | the workspace (`scope: workspace` app-data) | nothing — uses it |
| Seed data (alice/bob, notes) | `resources load demo` (a workspace job) | asserts against it |
| Allocated ports | the operator's port pool | reads them from the env |
| Browser profile / Playwright port | the workspace (`playwright` pool, `chrome` profile) | reused for a persistent browser (optional) |
| Login | the GraphQL `login` mutation (owned by the app auth/data provider seam) | calls it, persists `storageState` |

This is the constitution's *find the owner* rule applied to testing: the
workspace owns the environment, `resources` owns the seed, the app auth/data
provider seam owns the login contract, and Playwright owns the browser. The
harness only wires them.

## What ships in the framework

Two pieces, at the two levels that own them:

- **`@angee/e2e`** (this repo's `e2e/`) — the inherited harness. A consumer's
  `playwright.config.ts` is one line. It provides:
  - `defineE2EConfig()` — the framework Playwright config. `baseURL` is read from
    the workspace environment, so one config drives every workspace unchanged. It
    declares a `setup` project (authenticates roles → `storageState`) and a
    `chromium` project that depends on it.
  - `test` / `expect` — Playwright's `test` extended with an `api` fixture (a
    GraphQL caller bound to the test's session, mirroring the SPA's own transport:
    session cookie + CSRF header). **Import these from `@angee/e2e`, never from
    `@playwright/test` directly**, so the whole suite shares one Playwright
    instance (avoids the "Requiring @playwright/test second time" dual-instance
    trap when a workspace package re-exports fixtures).
  - `loginViaApi(request, creds)` + `roleStatePath(role)` — log a role in over the
    API and persist its `storageState`, used by the setup project.
  - `PageObject` — the base for the **Page Object Model**, Angee's default
    authoring style (see below).
- **Reference specs** (the sibling `angee-examples` repo's `e2e/`) — the worked example a consumer
  copies. `playwright.config.ts`, an `auth.setup.ts` that authenticates the seeded
  `alice`/`bob`, Page Objects under `pages/`, and specs under `tests/`.

A consumer addon adds e2e by creating its own `<project>/e2e` package that depends
on `@angee/e2e`, points `playwright.config.ts` at `defineE2EConfig()`, and writes
`*.spec.ts` files. No harness code is re-derived per project.

## Authoring style: Page Object Model

The default — and only framework-blessed — authoring style is **codegen-to-
bootstrap + Page Object Model**. A Page Object is the single source of truth for
one page's selectors and intents; specs read like prose and never re-derive a
selector. Bootstrap new flows with `playwright codegen`, then lift the recording
into a Page Object.

BDD/Gherkin and AI/natural-language scenario tools are **not** the default. They
add an indirection layer that fights *prefer deletion to abstraction*, and (for
AI) determinism the framework will not stake CI on. If a product team wants them,
they belong in an **optional, opt-in addon** layered over `@angee/e2e`'s
fixtures — never inherited by every project.

## Isolation depth

The workspace database is shared across a single run, not reset per test. That is
the deliberate "lighter easy lift" tradeoff:

- **Read-only assertions** run against the seeded demo data.
- **Mutating specs** must clean up after themselves (create → assert → delete) or
  create uniquely-named data, so order does not matter.
- **Concurrent writes are handled by project settings, not the harness.** SQLite locks
  the whole file on write, which surfaces as "database is locked" under parallel
  access. The fix lives at the owner — the project's `DATABASES["OPTIONS"]`
  (the stack host's `settings.yaml` + `angee.compose.defaults`) enables WAL, an `IMMEDIATE`
  transaction mode, and a busy `timeout`, so concurrent readers and writers wait
  rather than fail. The harness adds no serialisation of its own.

Per-worker or per-test database isolation is intentionally **not** built yet. If
parallel mutation flakiness ever demands it, the seam to add it is the workspace
(a per-worker database), not the harness.

**Assert invariants, not seed counts.** The demo seed grows over time (alice has
dozens of notes, not three). Specs assert durable invariants — a known record is
present, two users' scopes are disjoint, an anonymous write is denied with
`PERMISSION_DENIED` — never a volatile row count. See
the angee-examples suite's `e2e/tests/notes.spec.ts`.

## Running e2e

### Against the seeded framework-dev stack (the supported path)

```sh
# 1. Bring the seeded stack up (provision seeds the demo data).
angee --root "$angee_root" dev

# 2. Once, install the browser binaries.
pnpm --filter @angee-example/notes-e2e exec playwright install chromium

# 3. Run the suite against the stack's UI port.
ANGEE_UI_PORT=<ui-port> pnpm --filter @angee-example/notes-e2e test:e2e
```

`angee ws status` prints the allocated ports. The frontend service honours
`ANGEE_UI_PORT`, and `@angee/e2e` reads the same variable for `baseURL`, so the
browser drives exactly the SPA the workspace is serving. Override the whole URL
with `E2E_BASE_URL` if needed.

### Against a containerized stack with a remote browser

Point the suite at the frontend URL as seen **from the browser** and connect to
the edge-routed Playwright browser server:

```sh
E2E_BASE_URL=http://frontend:5301 \
E2E_WS_ENDPOINT=ws://localhost:8081/playwright-server/ \
E2E_WS_TOKEN=<operator-route-bearer> \
pnpm --filter @angee-example/notes-e2e test:e2e
```

When the browser server runs in the Compose network, the browser-visible URL
uses Compose DNS (for example, `http://frontend:5301`). Only browser-backed
fixtures use the websocket connection: Playwright `request` fixtures, including
setup authentication, still run in the driver process, and `storageState` files
stay local to the runner. The driver must therefore also be able to reach the
configured frontend URL (for example, by running in the Compose network).

### Against an already-running stack (quick local loop)

With `angee dev` already up from the stack root (UI on `5173`):

```sh
pnpm --filter @angee-example/notes-e2e exec playwright install chromium   # once
pnpm --filter @angee-example/notes-e2e test:e2e                           # defaults to :5173
pnpm --filter @angee-example/notes-e2e report                             # open the HTML report
```

## Environment contract

The harness reads these environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `ANGEE_UI_PORT` | Port the Vite SPA serves on | `5173` |
| `E2E_BASE_URL` | Full SPA origin (overrides `ANGEE_UI_PORT`) | derived |
| `E2E_WS_ENDPOINT` | Playwright browser-server websocket URL | local browser |
| `E2E_WS_TOKEN` | Operator route bearer for the websocket edge | unset |
| `CI` | Enables retries, `forbidOnly`, fail-fast reporter | unset |

GraphQL and CSRF are reached **through the SPA origin** (`/graphql/public/`,
`/auth/csrf/` via the Vite proxy), exactly as the browser does, so the session
cookie the specs persist is the one the browser sends.

## CI

Browser e2e is **excluded from the default test run**: `pnpm -r test` runs
Vitest, including the `@angee/e2e` harness unit tests, while a consumer suite
exposes its browser run separately as `test:e2e`. A dedicated GitHub Actions job
owns that run: create a workspace, `angee dev`, `playwright install`, run the
suite, and upload the HTML report + traces as artifacts. Because the workspace
gives the job its own database and ports, parallel CI runs do not collide.

> **Operator job (pending live validation).** The intended one-command path is an
> opt-in operator job that runs `playwright test` after the `django` and `frontend`
> services report healthy, threading `playwright_port` and the persistent
> `playwright_user_data_dir` the workspace already allocates. It is specified here
> but not added to the default `angee dev` graph (dev boots must not run the
> browser suite on every start); wiring and validating it against a live operator
> is the next step.
