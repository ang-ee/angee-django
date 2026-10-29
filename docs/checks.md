# Development Checks

This guide owns verification commands and their execution context. The
[backend](backend/guidelines.md) and [frontend](frontend/guidelines.md) guides own
what behavior needs coverage; manifests and CI own the executable scripts.
Run focused checks while editing, then the relevant broad checks before handoff.
Do not weaken a failing check or claim a check ran when its environment was absent.

## Choose The Working Directory

A **framework source root** contains this repository's `pyproject.toml`,
`packages/`, and `tests/`. A **stack root** contains the controlling `angee.yaml`
and the rendered host's `manage.py`. They are different directories in a
materialized workspace. Resolve `angee_root` with the
[workspace skill](../.agents/skills/angee-workspace/SKILL.md#resolve-the-controlling-stack-root).
Never initialize a stack under a source checkout.

Use the environment's `uv` and the repository's locked Python dependencies.
Use the owning stack's existing JS install for materialized source slots;
never `pnpm install` inside a slot or a nested package. A standalone CI checkout
installs its own repository workspace instead. Dependency changes update the
owning manifests and lockfiles through their normal workflow; verification
must not silently change them.

Commands below using `python -m` invoke the selected environment's modules; this
is a portable convention, not a claim that ordinary tool entrypoints are broken.
The pnpm examples disable automatic dependency installation during scripts.
Missing dependencies should be installed explicitly at their owning root.

## Python And Composition Contracts

Run from the **framework source root**, using the supported Python version and
locked dependencies from `pyproject.toml`/`uv.lock`:

| Scope | Command |
|---|---|
| Focused Python test | `uv run --locked python -m pytest -q tests/<test_file>.py` |
| Python/addon/template handoff: full SQLite suite; required for core changes | `env -u DATABASE_URL uv run --locked python -m pytest -q -n auto --dist loadfile --durations=25` |
| Randomized parallel isolation proof | `uv run --locked python -m pytest -q -n auto --dist loadfile -p randomly --randomly-seed=137 --durations=25` |
| Python lint | `uv run --locked python -m ruff check . --no-cache` |
| Python types | `PYTHONPATH=core:addons uv run --locked python -m mypy core/angee addons` |
| Dead-code review | `uv run --locked python -m vulture` |

Mypy imports addon-owned plugins before its Django settings host runs. The
invocation supplies the declared source roots on native `PYTHONPATH` so those
plugin modules can load; `mypy_path` controls static resolution and does not
change the interpreter's import paths. Consumer CI passes the framework's `core/`
and source roots and the typing host's declared `ANGEE_ADDON_DIRS` to its mypy
subprocess.

The consumer CI lane uses the same framework mypy policy and
[`tests.typing_settings`](../tests/typing_settings.py), with its existing
[`tests.typing_host`](../tests/typing_host.py) projection of native history
models. An explicit `ANGEE_PROJECT_DIR` reads the project's declared app roots
and addon directories through `ProjectContract.load`; the typing host retains
only those composition inputs and uses SQLite in memory, a synthetic secret,
and `<project>/.mypy_cache/typing_runtime`. Without that explicit project, the
host checks the full framework composition as before. The CI target is the
consumer's entire `addons` directory, including optional addons when their
declared composition is checked; it does not select only recently changed files.

A declared addon transition is a temporary executable input: its materialized
copy carries a source fingerprint until it is applied and the stack's history is
consolidated. Native mypy/Ruff discovery excludes those bodies; integrity-test
imports remain followed silently so their types are available. Do not change an
applied copy or digest to fix checker diagnostics. The
[runtime-migration mechanism tests](../tests/test_runtime_migrations.py) cover
materialization, fingerprints, replay and guarded history replacement through
neutral fixtures. Completed transition declarations and bodies are retired after
the [snapshot/reset cutoff](composer.md#foundation-and-provenance-upgrade-cutoff),
not retained indefinitely as compatibility APIs. Generated `typing_runtime`
model declarations remain followed because they are required typing inputs.

[`tests/test_layering.py`](../tests/test_layering.py) guards framework import
boundaries and optional state-field declarations. See the
[Database routing rule](backend/guidelines.md#rules) for persistence checks.

The PostgreSQL lane in [reusable checks](../.github/workflows/reusable-checks.yml)
covers workflow definition, publication, execution,
[settlement recovery](../tests/test_workflows_fix_round.py), authorization, concurrency,
test-harness and composed-consumer contracts, plus decision
[lifecycle](../tests/test_decisions_lifecycle.py) and
[concurrency](../tests/test_decisions_concurrency.py). This named file selection
also covers [review waiters](../tests/test_workflows_review.py), their
[locking races](../tests/test_workflows_review_concurrency.py),
[execution links](../tests/test_workflows_review_graphql.py), and
[inbox predicates](../tests/test_decisions_inbox.py),
[committed timeline waits and inherited record feeds](../tests/test_timeline_workflows.py), plus the
[agent session loop](../tests/test_agent_sessions.py) and its
[locking races](../tests/test_agent_sessions_concurrency.py), plus
[connection discovery races](../tests/test_integrate_discovery_concurrency.py),
[cross-addon reply and inbound joins](../tests/test_framework_integration.py),
[delivery contracts](../tests/test_messaging_delivery.py) with PostgreSQL
[sweep, claim-fence and held-action races](../tests/test_messaging_delivery_concurrency.py), and the
[comment reply workflow and scheduling races](../tests/test_workflows_posts.py). The selection
runs with four xdist workers grouped by file, `--nomigrations`, a JUnit report,
and a zero-skip gate. Pytest-django creates a separate PostgreSQL test database
for each worker. The composed-host subprocess tests use Django's test runner to
create separate worker-named secondary test databases; every caller bounds the
subprocess with the one hang limit in [`composed_host.py`](../tests/composed_host.py). The full
suite runs on SQLite with migrations enabled and `DATABASE_URL` unset; do not
apply the PostgreSQL lane's settings to the full suite. PostgreSQL-only modules
skip explicitly on SQLite. Report those skips separately from the PostgreSQL
lane's executed coverage.

Local pytest remains serial and keeps its normal ordering: `addopts` disables
pytest-randomly and does not select workers. An explicit `-p randomly` re-enables
the plugin; change `--randomly-seed` to reproduce or vary test order. Use `-n 0`
or `-p no:xdist` for serial debugging. `--dist loadfile` keeps each test module on
one worker. Pytest-django isolates worker databases; custom database and composed
host fixtures must use process-local connections or pytest's worker-local
`tmp_path`/`tmp_path_factory`, and restore registry changes after each test.

### Source-addon Test Models

Share source-addon compositions through their owning test apps:
[`angee.resources.testing`](../addons/angee/resources/testing/__init__.py),
[`angee.workflows.testing`](../addons/angee/workflows/testing/__init__.py), and
[`angee.integrate.testing`](../addons/angee/integrate/testing/__init__.py). Their
package docstrings own the adoption contract. Other reusable concrete
compositions, including [`decisions`](../addons/angee/decisions/testing/models.py)
and [`messaging`](../addons/angee/messaging/testing/models.py), register once
through imports in the root conftest before database setup. The framework-generic
[`composed_tables`](../addons/angee/base/testing/fixtures.py) fixture uses native transactional
isolation and synchronizes REBAC after each flush; use the native `db` fixture
when a test needs neither transaction behavior nor permission synchronization.
Composed hosts also install their schema during native database setup. Register
`angee.base.testing.fixtures` through the root conftest's `pytest_plugins`; a runner
that loads it explicitly must pass `-p django -p angee.base.testing.fixtures` in that
order so pytest-django cannot shadow the shared `django_db_setup` override.
Hosts installing provenance also register
[`angee.provenance.testing.fixtures`](../addons/angee/provenance/testing/fixtures.py)
for test attribution; the generic framework plugin does not install that boundary.
A test that reaches a statement source needs the install-tier `provenance.Rule`
principals parties declares: list
[`angee.parties.testing.fixtures`](../addons/angee/parties/testing/fixtures.py) in
`pytest_plugins` and request its `statement_sources` fixture (the framework's own
hosts load them after every REBAC sync).

Workflow tests reach execution states through the shared `load_workflow`,
`start_run`, `run_until`, `decide` and `run_factory(...).at(...)` drivers. These compose
production admission and transition verbs; do not fabricate step runs or attempts
to stand in for execution. The scoped `capture_tasks` and `observe(model)`
helpers capture task sends and publications. Declared trigger sources bind
through their native app lifecycle. Decisions-only suites use
`angee.decisions.testing` drivers with central concrete models. Name Python
test modules `test_<concern>.py` and tests
`test_<behavior>` so native discovery and focused file selection agree.

### Owner Search

Before adding a backend abstraction, search for the native owner first. Run the
scans from the **framework source root**; narrow with paths or `-g` globs when
the hit list is long:

- `rg "AppConfig|schemas|permissions|resources|autoconfig"` finds the addon
  declaration and composition owners: app configs, schema buckets, permission
  and resource manifests, autoconfig.
- `rg "QuerySet|Manager.from_queryset"` finds the manager or queryset that
  already owns a row rule or read policy.
- `rg "apps.get_model|get_app_configs"` finds existing registry lookups, so a
  new one composes the same app-registry seam instead of a private registry.

The [design skill](../.agents/skills/angee-design/SKILL.md) owns the owner-map
procedure these scans feed.

### Outbound TLS Trust

Checks that make real outbound HTTPS calls (real-socket transport tests, OAuth
round trips, bridge probes) need a CA store in the environment that runs them;
the [TLS contract](backend/guidelines.md#integration-and-worker-contracts) keeps
trust there, never in call sites. A dev mac trusts through Homebrew
`ca-certificates`. Repair a minimal container or a bare CI/agent sandbox once,
at bootstrap of the shell that runs the checks, by installing the OS
`ca-certificates` package or pointing OpenSSL at the selected environment's
bundled store:

```sh
export SSL_CERT_FILE="$(python -m certifi)"
```

## Agent Methodology And Documentation

For changes limited to instructions, tool adapters, or their tests, run from the
**framework source root**:

```sh
uv run --locked python .agents/tools/sync-adapters.py --check
uv run --locked python -m pytest -p no:django --noconftest -c /dev/null --rootdir=. -q tests/test_agent_stack_root_policy.py tests/test_agent_work_state_policy.py tests/test_agent_methodology.py tests/test_documentation_contracts.py tests/test_owner_index.py tests/test_agent_owner_map_guard.py
git diff --check
```

These tests exercise documentation links/commands, template input agreement,
adapter parity, and mocked helper behavior without starting the application.
`tests/test_owner_index.py` validates the owner index citations: every cited
`path:line` in `.agents/skills/angee-design/references/owners.md` declares the
named symbol, and every public class a foundation models or fields module
declares, and every addon manifest, has a row.
Disabling pytest-django, application configuration, and conftest intentionally
isolates these tests; use normal pytest for application or template behavior.
If template source changes too, run its contract tests and the Python suite above. A prose-only
change does not require starting a browser. Review rewritten rules semantically:
passing link and policy tests does not prove the prose agrees with architecture.

## Composition And Schema

`angee --root "$angee_root" dev` starts the complete local stack. Do not start
Django, Vite, ASGI servers, workers, or watchers separately. The following
one-shot commands run from the **stack root**, through its host environment.
They are distinct lifecycle operations, not a mandatory sequence for every task:

Install the environment only on a new or stopped host. For a running stack, use
its declared restart job through the operator; see
[Restart the running stack](howto/getstarted.md#restart-the-running-stack).
Management checks use `--no-sync` so invoking one cannot install or rebuild first.

| Purpose | Command | Prerequisite / effect |
|---|---|---|
| Install for development | `uv sync` | Installs native editable distributions on a stopped/new host; does not compose |
| Build runtime for development | `uv run --no-sync python -m angee.build --root . --output runtime` | Explicit fresh-process compilation after dependency sync; no database operations |
| Install for production | `uv sync --no-editable` | Installs native wheels; does not compose |
| Build runtime for production | `uv run --no-sync python -m angee.build --root . --output runtime --check-history` | Validates tracked history offline before provision |
| Build distribution wheels | `uv build --wheel --all-packages` | Native workspace package builds; runtime generation is a separate host job |
| Prepare the installed runtime | `uv run --no-sync python manage.py angee provision` | One prebuilt native process; the [command owner](../core/angee/management/commands/angee.py) defines ordering and options |
| Clean generated runtime | `uv run --no-sync python manage.py angee clean` | [Guarded whole-tree cleanup](composer.md#runtime-cleanup); preserves current migration directories |
| Check composition drift | `uv run --no-sync python manage.py angee check` | Explicit read-only check of composer artifacts and addon upgrade declarations; startup never repairs output |
| Author schema migrations | `uv run --no-sync python manage.py makemigrations <app-labels>` | After editable build; review and commit `runtime/<label>/migrations/` before packaging |
| Apply migrations | `uv run --no-sync python manage.py migrate` | Operates on the selected stack database |
| Sync permissions | `uv run --no-sync python manage.py rebac sync` | After migrations when permissions change; `rebac sync --prune-stale` also retires removed definitions, the shape provision runs ([REBAC](backend/guidelines.md#rebac)) |
| Load declared resource data | `uv run --no-sync python manage.py resources load` | After migrations when resource data changes |
| Validate runtime contracts | `uv run --no-sync python manage.py check` | Includes construction of every named GraphQL schema; run against deployment code and settings before starting writer tiers |
| Emit GraphQL SDL | `uv run --no-sync python manage.py schema` | Uses the emitted concrete models; provision reuses its prebuilt registry and checked schemas |
| Check GraphQL SDL | `uv run --no-sync python manage.py schema --check` | Separate from the composer drift check |

Run only lifecycle steps needed for the change and within the user's authorized
scope.
For containerized stacks, use the declared service/job environment; a host shell
without the runtime dependencies cannot run these commands directly.

After a model change, rebuild through the native runtime-build job and author
required native history before provision. Emit SDL, then run the host web
package's codegen **before** checking addon fragments. From the stack
root, for the default `web_path=web`:

```sh
pnpm --config.verify-deps-before-run=false --dir web run codegen
```

Use the rendered `web_path` if customized. Root test/typecheck scripts do not
implicitly perform codegen. After a manifest/dependency change, synchronize at
the stack's dependency owner before codegen; never install in the source slot.

## Frontend Packages And Addon Fragments

The stack's JS dependencies must already be installed. Framework packages are
schema-independent; addon fragments need the composed host's generated documents.

Name frontend test files `<owner>[.<concern>].test.ts` (or `.tsx` for JSX), beside
the owner, and name each test for observable behavior. Use `.native.test.tsx`
when a separate suite exercises real provider integration; compose the existing
[`createUiTestProviders`](../packages/ui/src/testing.tsx) or
[`createRefineTestProviders`](../packages/refine/src/testing.tsx) harness instead
of copying provider setup. These names share the same Vitest discovery and
focused-file selection convention.

Waiting queries (`findBy*`, `waitFor`) share one limit, set in the published
[Vitest setup](../packages/app/config/vitest-setup.ts) to cover a runner's cold
first render. Do not pass a per-test timeout for a slow render: split a wait that
covers several reads into one wait per read, or raise the shared value with
measurements.

| Scope | Working directory | Command |
|---|---|---|
| Focused package test/typecheck/build | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter <package-name> run <script>` |
| Framework package types | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' run typecheck` |
| Framework package tests | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' run test` |
| Published package builds | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' --filter '!@angee/storybook' run build` |
| Distribution import checks | Framework source root, after builds | `node packages/scripts/check-dist-imports.mjs` |
| UI export checks | Framework source root, after builds | `node packages/ui/scripts/check-views-exports.mjs` |
| Package/exports architecture and the UX component ontology guard (addons compose `@angee/ui` owners: no raw elements, lucide renders, deleted classes or owners, locale bypasses, local recipes or tone maps; covers `../angee-arp` and `../angee-phrogg` when materialized) | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter @angee/app exec vitest run src/architecture-guardrails.test.ts` |
| Design-system catalog and token-name drift | Stack root | `pnpm --config.verify-deps-before-run=false --filter @angee/storybook --fail-if-no-match run catalog:check` |
| All composed fragment types | Stack root, after SDL/codegen | `pnpm --config.verify-deps-before-run=false --dir web run typecheck` |
| All composed fragment tests | Stack root, after SDL/codegen | `pnpm --config.verify-deps-before-run=false -r run test` |

After changing export annotations, story owners or token CSS, regenerate with
`pnpm --config.verify-deps-before-run=false --filter @angee/storybook --fail-if-no-match run catalog:generate`
from the stack root.
The UI catalog test exercises the generator's drift-check CLI; Storybook's
`catalog:check` also verifies specimen reachability, guideline story links and
the token deletion findings.

For page or addon changes, run a primitive-drift scan and explain every hit
outside `@angee/ui`. A hit is not automatically wrong, but it must either compose
the shared primitive or identify the owning framework gap to fix first. From the
framework source root; include `../angee-messaging-bridges/addons` in the scan
when that optional slot is present and affected:

```sh
rg -n '<table\b|<thead\b|<tbody\b|<tr\b|<td\b|<th\b|role="grid"|useReactTable|manualPagination' packages addons examples -g '*.tsx'
rg -n 'useAuthored(Query|Mutation)<|interface .*Data|interface .*Variables|fetch\([^)]*graphql|gql`' packages addons examples -g '*.ts' -g '*.tsx'
```

Run the architecture guardrail above when changing package layering, public
shared owners, or addon manifests.

The framework root has no generic `build` script. Package-filtered builds and the
distribution checks above are the handoff path for published surfaces. A package
script can be invoked only if its own `package.json` defines it.

## Browser Verification

Meaningful UI changes require exercising the affected behavior in the live app,
including relevant loading, failure, permissions, and interaction states. Use
existing authorized browser tooling and the selected stack URL. Typecheck,
Vitest, and a successful HTTP response do not establish that a page works.

The notes reference suite requires a live, seeded stack with `example.notes`
composed (the framework-dev `full` profile). From the **stack root**:

```sh
pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter @angee-example/notes-e2e exec playwright install chromium
ANGEE_UI_PORT=<ui-port> pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter @angee-example/notes-e2e run test:e2e
pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter @angee-example/notes-e2e exec playwright show-report
```

The install step is needed only if the browser binary is absent. Set
`E2E_BASE_URL` for a non-default origin. See the [e2e guide](frontend/e2e.md) for
fixtures, remote browsers, and authoring. Use a test stack with suitable data:
creating a source workspace alone does not allocate a separate running host or
database, and these commands do not create one.

## What CI Actually Runs

[CI](../.github/workflows/ci.yml) calls
[reusable checks](../.github/workflows/reusable-checks.yml) on pull requests and
pushes to `main`. The current tiers are:

- **Python quality:** Ruff over the source tree and mypy over core and addons,
  using the locked framework environment.
- **SQLite:** the full framework Python suite once, as six shards that
  pytest-split cuts into contiguous chunks of equal stored time from the
  committed [durations](../.test_durations), so each test file stays in one or two
  shards. Each shard runs
  `-n auto --dist loadfile` with a 60-minute limit and prints its 25 slowest test
  durations, with migrations enabled and `DATABASE_URL` unset. Structural tests
  remain part of this suite. Per-file scheduling keeps a file on one worker, so the
  heaviest files (the proposals access, owner-gate adopters and agents GraphQL
  campaigns, about 700 stored seconds each) set a floor near 25 minutes on CI.
  Their cost is per-test fixture setup, a `rebac sync` before every test; making
  that setup cheaper is how the limit comes down.
- **PostgreSQL:** in three shards of its own beside the SQLite ones, split the
  same way and with a 40-minute limit, the named workflow, decision and agent session
  selection runs with `-n 4 --dist loadfile --nomigrations` and prints its 25
  slowest test durations; each shard writes JUnit output and checks that it did
  not skip tests. Its server runs with `max_locks_per_transaction = 256`, since
  each worker creates every table in one transaction. The `uv` job passes only
  when Python quality and every shard of both test suites pass, so callers keep
  one check name.
- **Packages:** framework package typecheck/test/build and export/distribution
  checks.
- **Composed stack:** compose the host, check generated documents
  and addon fragments, and run the bridges repository's pytest suite with its
  native source-test settings and collection policy. Bridge tests use the
  composed stack's runtime dependencies plus the framework's locked check tools,
  as the consumer lane does; they do not resolve a second environment from the
  bridges' standalone generated addon dependency group. Its checked-in group
  remains subject to the suite's manifest-drift guard.
  Framework composed-host tests create their own temporary hosts in the SQLite
  suite; the stack job does not repeat that framework suite.

A test missing from the durations still runs: pytest-split gives it the average
duration and places it in exactly one shard. Refresh the file when a shard nears
its limit or its balance drifts (the shards' printed estimates against their wall
times): run `uv run pytest -q -n 4 --dist loadfile --store-durations` in the
framework checkout and commit `.test_durations`. Ties in the assignment fall back
to collection order, so an opt-in `-p randomly` run must give every shard one
shared `--randomly-seed`.

Separate structural and downstream tiers are not implemented. Separate workflows
enforce private-path exclusion and other repository policies. Executable jobs in
the reusable workflow own timeouts; GitHub's reusable-workflow caller syntax does
not accept `timeout-minutes`.

Consumer repositories call the same reusable checks with `uv-enabled: false`,
`pnpm-enabled: false`, and `stack-enabled: true`. Their caller owns the explicit
`consumer-sources` (repository, sibling slot path, ref), `consumer-roots`,
`consumer-path`, `consumer-tests`, and optional `consumer-pytest-plugins` inputs.
The caller revision replaces its source ref; dependency revisions remain explicit.
The full declared Python collection runs once across six `stack-shard` jobs,
using native pytest-split `least_duration` assignment. Without stored consumer
timings, the plugin balances test counts with equal weights. Each shard renders
its own host, resolves native manifest dependencies, composes/provisions it, and
collects the declared Python paths with `python_files=test*.py` on PostgreSQL.
Four native pytest-xdist workers use `--dist worksteal` inside each shard, so long
consumer intake modules can use every worker. Each worker loads the configured
host through generated `runtime.settings`; pytest-django owns its separate test
database. Pytest emits a thread dump after five minutes in one test protocol,
including setup and teardown, without changing its outcome. Shared test plugins
must defer model imports until Django's app registry is populated. Check tools
come from the framework lockfile.

Group 1 runs consumer Ruff/mypy and, after host SDL/codegen, the consumer's web
fragment typechecks and tests. Optional full-profile bridge tests also run only
in group 1. Calls without a consumer use one execution job. The `stack` aggregate
passes only when every execution job succeeds, preserving one check name for
callers. The framework and other consumers retain their own caller lanes.

The optional `ADDON_SOURCES_TOKEN` secret must read every private source declared
by a caller; the default GitHub token only reads the calling repository. Configure
that secret in each consumer repository before expecting private cross-repository
checkout to pass. `BRIDGES_TOKEN` retains its separate full-profile purpose.

Browser e2e is manual; neither `pnpm -r test` nor the current GitHub workflows run
it. Vulture remains a prescribed local review, outside CI.
State local evidence and CI evidence separately. When
changing CI, reconcile this section and link to the actual job instead of
promising planned coverage as though it already exists.

### Consumer CI Publication

Consumer callers use `main` for the reusable workflow, framework checkout, and
source dependencies. The caller's own source always uses its exact `github.sha`;
its declared source ref is replaced by that revision. Workspace branches do not
define the durable CI dependency contract.

Publish in dependency order:

1. Publish required upstream fixes and record their versions or immutable Git
   revisions in the framework dependency declarations and lockfile.
2. Land the framework's reusable workflow and required composition changes on
   `main` before publishing consumer callers that use the new inputs.
3. Land source dependency changes on their `main` branches before publishing
   dependent consumer callers. A caller tests its own review revision against
   those already-landed dependencies.

Local source checks do not establish that this publication chain or a remote CI
run has completed.
