# End-to-End Testing

End-to-end (e2e) tests drive the **real, composed product** — the Vite/React SPA
talking to the running Django + GraphQL backend — and assert what a user (human
or agent) actually sees. They are the top of the testing pyramid: slow, few, and
high-signal. Unit and integration coverage lives elsewhere (pytest for the
backend, Vitest for the frontend); this page owns the browser layer.

`docs/stack.md` locks **Playwright** as the browser engine. This page owns *how
Angee uses it* and what the framework ships so a consumer addon gets e2e for
free.

## The owning idea: a workspace is the test environment

Angee already owns environment isolation. An **angee workspace** materialises an
isolated inner stack with its **own database**, its **own allocated ports**
(`django`, `ui`, `playwright`, …), and a **persistent browser profile** — all
declared in `templates/workspaces/dev/copier.yml`. Bringing the workspace up runs
the full deterministic bootstrap (`build → makemigrations → migrate → rebac sync
→ resources load install,demo → schema`), which **seeds the demo data** the tests
assert against.

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

- **`@angee/e2e`** (`packages/e2e`) — the inherited harness. A consumer's
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
- **Reference specs** (`examples/notes-angee/e2e`) — the worked example a consumer
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
  (`examples/notes-angee/settings.yaml` + `angee.compose.defaults`) enables WAL, an `IMMEDIATE`
  transaction mode, and a busy `timeout`, so concurrent readers and writers wait
  rather than fail. The harness adds no serialisation of its own.

Per-worker or per-test database isolation is intentionally **not** built yet. If
parallel mutation flakiness ever demands it, the seam to add it is the workspace
(a per-worker database), not the harness.

**Assert invariants, not seed counts.** The demo seed grows over time (alice has
dozens of notes, not three). Specs assert durable invariants — a known record is
present, two users' scopes are disjoint, an anonymous write is denied with
`PERMISSION_DENIED` — never a volatile row count. See
`examples/notes-angee/e2e/tests/notes.spec.ts`.

## Running e2e

### In a dedicated workspace (the supported path)

```sh
# 1. Materialise an isolated, seeded stack (unique DB + ports + browser profile).
# Resolve angee_root and work_state_path with .agents/skills/angee-workspace/SKILL.md.
angee --root "$angee_root" ws create e2e --template dev \
  --input base_ref=<branch> --input example=notes-angee \
  --input work_state_path="$work_state_path"
cd "$angee_root/workspaces/e2e"
angee dev                      # brings the seeded stack up; note the allocated ui port

# 2. Once, install the browser binaries for this workspace.
pnpm --filter @angee-example/notes-e2e exec playwright install chromium

# 3. Run the suite against the workspace's allocated UI port.
ANGEE_UI_PORT=<ui-port> pnpm --filter @angee-example/notes-e2e test:e2e
```

`angee ws status` prints the allocated ports. The frontend service honours
`ANGEE_UI_PORT`, and `@angee/e2e` reads the same variable for `baseURL`, so the
browser drives exactly the SPA the workspace is serving. Override the whole URL
with `E2E_BASE_URL` if needed.

### Against an already-running stack (quick local loop)

With `angee dev` already up at the repo root (UI on `5173`):

```sh
pnpm --filter @angee-example/notes-e2e exec playwright install chromium   # once
pnpm --filter @angee-example/notes-e2e test:e2e                           # defaults to :5173
pnpm --filter @angee-example/notes-e2e report                             # open the HTML report
```

## Environment contract

The harness reads only these, all exported by the workspace/stack:

| Variable | Meaning | Default |
|---|---|---|
| `ANGEE_UI_PORT` | Port the Vite SPA serves on | `5173` |
| `E2E_BASE_URL` | Full SPA origin (overrides `ANGEE_UI_PORT`) | derived |
| `CI` | Enables retries, `forbidOnly`, fail-fast reporter | unset |

GraphQL and CSRF are reached **through the SPA origin** (`/graphql/public/`,
`/auth/csrf/` via the Vite proxy), exactly as the browser does, so the session
cookie the specs persist is the one the browser sends.

## CI

e2e is **excluded from the default test run** (`pnpm -r test` runs Vitest only;
the e2e package exposes `test:e2e`, not `test`). A dedicated GitHub Actions job
owns it: create a workspace, `angee dev`, `playwright install`, run the suite,
and upload the HTML report + traces as artifacts. Because the workspace gives the
job its own database and ports, parallel CI runs do not collide.

> **Operator job (pending live validation).** The intended one-command path is an
> opt-in operator job that runs `playwright test` after the `django` and `frontend`
> services report healthy, threading `playwright_port` and the persistent
> `playwright_user_data_dir` the workspace already allocates. It is specified here
> but not added to the default `angee dev` graph (dev boots must not run the
> browser suite on every start); wiring and validating it against a live operator
> is the next step.
