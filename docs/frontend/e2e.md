# End-to-End Testing

End-to-end (e2e) tests drive the **real, composed product** — the Vite/React SPA
talking to the running Django + GraphQL backend — and assert what a user (human
or agent) actually sees. They are the top of the testing pyramid: slow, few, and
high-signal. Unit and integration coverage lives elsewhere (pytest for the
backend, Vitest for the frontend); this page owns the browser layer.

[The opinionated stack](../stack.md) locks **Playwright** as the browser engine.
This page owns *how Angee uses it* and what the framework ships so a consumer
addon gets e2e for free.

## The owning idea: the stack supplies the test environment

The reference suite runs against a live framework-dev stack with `example.notes`
composed and its demo data seeded. The stack owns provisioning; the harness
connects to its allocated UI origin. A source workspace and a Playwright run do
not create a fresh database. Repeated runs against one stack share its data.

| Concern | Owner | The harness does |
|---|---|---|
| Database and runtime | the selected stack | uses the existing application |
| Seed data (alice/bob, notes) | the stack's resource-data provisioning | asserts against it |
| Allocated ports | the operator's port pool | reads them from the env |
| Browser process | Playwright, locally or through an explicitly configured remote server | creates test contexts and loads role storage state |
| Login | the GraphQL `login` mutation (owned by the app auth/data provider seam) | calls it, persists `storageState` |

This is the constitution's *find the owner* rule applied to testing: the
stack owns the environment, `resources` owns the seed, the app auth/data
provider seam owns the login contract, and Playwright owns the browser. The
harness only wires them.

## What ships in the framework

Two pieces, at the two levels that own them:

- **[`@angee/e2e`](../../packages/e2e/README.md)** — the inherited harness in
  `packages/e2e/`. A consumer's
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
- **Reference specs** (`examples/e2e/`) — the worked example a consumer copies.
  `playwright.config.ts`, an `auth.setup.ts` that authenticates the seeded
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

The stack database is shared across tests and runs. The harness does not reset
it or allocate a database per run or worker:

- **Read-only assertions** run against the seeded demo data.
- **Mutating specs** must clean up after themselves (create → assert → delete) or
  create uniquely-named data, so order does not matter.
- **Concurrent writes are handled by the database and project settings.** The
  harness runs tests in parallel. Diagnose transaction/locking failures at those
  owners; do not add harness serialization to conceal them.

Per-worker or per-test database isolation is intentionally **not** built yet. If
parallel runs need isolated state, provide separate stack/database environments
explicitly. Source-workspace names alone do not establish that isolation.

**Assert invariants, not seed counts.** The demo seed grows over time (alice has
dozens of notes, not three). Specs assert durable invariants — a known record is
present, two users' scopes are disjoint, an anonymous write is denied with
`PERMISSION_DENIED` — never a volatile row count. See
`examples/e2e/tests/notes.spec.ts`.

## Running e2e

[Checks](../checks.md) owns stack resolution, browser installation and the exact
suite/report commands. Select the running stack's UI port through `ANGEE_UI_PORT`
or set its full origin with `E2E_BASE_URL`. Do not assume the harness's fallback
port matches an allocated stack port.

For a remote browser, configure `E2E_WS_ENDPOINT` and, when the edge route requires
it, `E2E_WS_TOKEN`. `E2E_BASE_URL` must be reachable from both browser and driver.

When the browser server runs in the Compose network, the browser-visible URL
uses Compose DNS (for example, `http://frontend:5301`). Only browser-backed
fixtures use the websocket connection: Playwright `request` fixtures, including
setup authentication, still run in the driver process, and `storageState` files
stay local to the runner. The driver must therefore also be able to reach the
configured frontend URL (for example, by running in the Compose network).

## Environment contract

The harness reads these environment variables:

| Variable | Meaning | Default |
|---|---|---|
| `ANGEE_UI_PORT` | Port the Vite SPA serves on | `5173` |
| `E2E_BASE_URL` | Full SPA origin (overrides `ANGEE_UI_PORT`) | derived |
| `E2E_WS_ENDPOINT` | Playwright browser-server websocket URL | local browser |
| `E2E_WS_TOKEN` | Operator route bearer for the websocket edge | unset |
| `CI` | Enables one retry and `forbidOnly` | unset |

The executable owners are the harness's [environment](../../packages/e2e/src/env.ts)
and [configuration](../../packages/e2e/src/config.ts) modules.

GraphQL and CSRF are reached **through the SPA origin** (`/graphql/public/`,
`/auth/csrf/` via the Vite proxy), exactly as the browser does, so the session
cookie the specs persist is the one the browser sends.

## CI

Browser e2e is **manual and absent from the repository's CI**. Recursive unit
checks include the `@angee/e2e` harness tests; the reference browser suite exposes
its separate `test:e2e` script. Passing CI therefore does not establish browser
coverage of the composed application.

A future browser CI job must provision and seed its own stack/database, wait for
healthy services, run the suite and retain reports/traces. It must establish
isolation explicitly rather than assuming that each source workspace has a
different database. Browser runs also remain an explicit action rather than a
side effect of every development-stack boot.
