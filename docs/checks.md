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
installs its own repository workspace instead. Ordinary JS installation uses
`pnpm install --frozen-lockfile`. Author dependency changes with native
`pnpm install --lockfile-only --no-frozen-lockfile` at that same owner before
installing. Fresh stacks author an absent lock once; existing lock drift fails.
Instance frontend jobs keep their lock on the mounted project through pnpm
`--lockfile-dir /opt/angee-project`; pnpm owns the resulting modules/store layout
and package-local links. When intentionally changing instance workspace
membership, run native lock authoring in its declared image/workspace environment
with that same flag, then retain the project lock. Verification must not silently
change manifests or lockfiles.

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
| Changed-path selection: the test files a change reaches, as a pull request's CI picks them | `git diff --name-only <base> \| uv run --locked python scripts/changed_path_selection.py` |
| Python/addon/template handoff: full SQLite suite; required for shared foundation/composition changes | `env -u DATABASE_URL uv run --locked python -m pytest -q -n auto --dist loadfile --durations=25` |
| Randomized parallel isolation proof | `uv run --locked python -m pytest -q -n auto --dist loadfile -p randomly --randomly-seed=137 --durations=25` |
| Python lint | `uv run --locked python -m ruff check . --no-cache` |
| Python import boundaries | `uv run --locked lint-imports` |
| Python types | `PYTHONPATH=src uv run --locked python -m mypy src` |
| Dead-code review | `uv run --locked python -m vulture` |

Mypy loads native compiler and addon-owned plugins through the declared Python
installation and its configured source roots. `mypy_path` controls static
resolution, not runtime imports. Use the owning CI invocation and typing settings;
do not introduce a source-model append, generated stub directory or parallel
model registry to make a missing native type visible. The composed typing host
is isolated from the running stack and pytest's bare source-model settings.

Projects author data upgrades as ordinary Django migrations with explicit
dependencies, historical models and the schema editor's database alias. Compose
preserves those files. The [native-history tests](../tests/test_native_initial_history.py)
cover initial authoring, project data operations and preservation across rebuilds;
the [migration-policy tests](../tests/test_runtime_migrations.py) cover Resources'
retirement policy and the native drop guard. Previously shipped transition sources
are historical upgrade assets only; retire them after the supported upgrade cutoff
and preserve Python imports still required by retained migration history.

Framework import boundaries are `[tool.importlinter]` contracts in
[`pyproject.toml`](../pyproject.toml), checked by `lint-imports`.
[`tests/test_layering.py`](../tests/test_layering.py) guards the runtime import
closures a static graph cannot see and optional state-field declarations. The
[Database routing rule](backend/guidelines.md#rules) is a system check: base's
`angee.E050` refuses any `DATABASE_ROUTERS`
([`tests/test_base_checks.py`](../tests/test_base_checks.py)).

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
[committed timeline waits and inherited timeline subscriptions](../tests/test_timeline_workflows.py),
[timeline stream authorization](../tests/test_timeline_subscriptions.py), plus the
[agent session loop](../tests/test_agent_sessions.py) and its
[locking races](../tests/test_agent_sessions_concurrency.py), plus
[connection discovery races](../tests/test_integrate_discovery_concurrency.py),
[cross-addon reply and inbound joins](../tests/test_framework_integration.py),
[delivery contracts with their sweep, claim-fence and held-action races and webform submission
races](../tests/test_composed_messaging.py), and the
[comment reply workflow and scheduling races](../tests/test_composed_workflows.py). The selection
also runs every remaining PostgreSQL-only contract: row locks, hierarchy, IAM,
[OIDC disconnect admission under the user lock](../tests/test_oidc.py),
knowledge, [pgvector test-database readiness](../tests/test_postgresql_extensions.py),
messaging with its [channel owner](../tests/test_messaging_channel_concurrency.py) races,
[portfolio report](../tests/test_portfolio_concurrency.py) locks, proposals,
sequences, spaces, storage with its
[window-aligned download URLs and expiry](../tests/test_storage_access_campaign.py),
the system queryset, work and [task ordering](../tests/test_composed_work.py) locks,
integrate streams, [scheduler row locks](../tests/test_integrate_scheduler.py) and workflow
integrate steps, the
[grouped rows](../tests/test_group_rows.py) partitions, the
messaging inbox's [explorer](../tests/test_inbox_explorer.py),
[thread and message reads](../tests/test_messaging_inbox_read.py) and
[part reads](../tests/test_inbox_part_reads.py), the composed member
[person/projects planner budgets](../tests/test_composed_examples.py), and the composed
[intake access decision](../tests/test_composed_intake.py). It also runs
[task ordering](../tests/test_task_rank_allocation.py),
[record tasks](../tests/test_projects_record_tasks.py), and
[project access and projections](../tests/test_projects_access_projections.py).
`test_connections.py`, `test_system_queryset.py`, `test_integrate_scheduler.py` and
`test_transitions.py` contribute only their PostgreSQL test nodes because each also has
SQLite-only tests;
the PostgreSQL branches in `test_extraction_models.py` and
`test_knowledge_access_campaign.py`, the [resource ledger's](../tests/test_resource_ledger.py)
concurrent upsert, and the composed profiles that carry PostgreSQL contracts also run by
node. Every other listed file runs whole, which keeps the SQLite
floor tests out of the PostgreSQL zero-skip gate. The six groups are assigned
by pytest-split's `least_duration` algorithm over the
[PostgreSQL timing map](../.test_durations_postgres); each runs with four xdist
workers grouped by file, `--nomigrations`, a JUnit report, and a zero-skip
gate. Pytest-django creates a separate PostgreSQL test database
for each worker. Composed-host tests compose and boot one host per profile, run all
of the profile's labels in it with Django's test runner on worker-named secondary
test databases, and report each test as a pytest subtest named by its Django test id;
[`run_composed_tests`](../tests/composed_host.py) owns that run and its one hang limit.
A profile lists its SQLite and PostgreSQL labels separately, so the PostgreSQL lane runs
only the composed PostgreSQL contracts and a skipped one fails its zero-skip gate. The full
suite runs on SQLite with migrations enabled and `DATABASE_URL` unset; do not
apply the PostgreSQL lane's settings to the full suite. PostgreSQL-only modules
skip explicitly on SQLite. Report those skips separately from the PostgreSQL
lane's executed coverage.

### SQLite Version

SQLite runs need SQLite 3.53 or newer. Earlier releases (3.51.2 and older were
checked) reject a subquery whose `ORDER BY` names an outer query's column and report
`no such column`; messaging's redirect-aware follower reads depend on that. Python
reports its library as `uv run --locked python -c "import sqlite3; print(sqlite3.sqlite_version)"`.
uv's managed CPython bundles a current SQLite, while a distribution's system Python
links the distribution's library (Ubuntu 26.04 and Debian trixie ship 3.46.1). CI sets
`UV_PYTHON_PREFERENCE: only-managed` for that reason; locally, pass `--managed-python`
or set the same variable when the system Python is older.

### Local PostgreSQL Prerequisites

PostgreSQL runs require the pgvector extension on the server. CI uses
`pgvector/pgvector:pg17`, retaining PostgreSQL 17. For a local Homebrew server,
install [pgvector](https://formulae.brew.sh/formula/pgvector), then connect to
that server as a PostgreSQL **superuser** and enable it in `template1` before
running tests:

```sh
brew install pgvector
psql -h localhost -U postgres -d template1 -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;'
```

Replace `postgres` with the local superuser role and use the host and port of
the server selected by `DATABASE_URL`. The server's
extension files must match its PostgreSQL major; installing the Python `pgvector`
package alone does not install the server extension. Django and pytest-django
create test databases from [PostgreSQL's default `template1`](https://www.postgresql.org/docs/17/manage-ag-templatedbs.html),
including the composed host's secondary databases. They inherit `vector` and `pg_trgm` even
under `--nomigrations`, which bypasses the addon’s native `pre_migrate`
extension receiver. That receiver is the single application extension owner;
`makemigrations` does not generate extension creation operations. A role
with `CREATEDB` can then run the tests without superuser privileges.

Existing databases do not inherit later changes to `template1`: recreate a
reused test database with pytest's `--create-db`, or have a superuser enable
`vector` in that database too. Enable it separately in an existing application
database before migrating the pgvector addon with a non-superuser role.

Local pytest remains serial and keeps its normal ordering: `addopts` disables
pytest-randomly and does not select workers. An explicit `-p randomly` re-enables
the plugin; change `--randomly-seed` to reproduce or vary test order. Use `-n 0`
or `-p no:xdist` for serial debugging. `--dist loadfile` keeps each test module on
one worker. Pytest-django isolates worker databases, suffixing the source host's
stable `.test-db/` SQLite name per worker, so `--reuse-db` keeps them between runs
and `--create-db` rebuilds them after a model change; concurrent runs need separate
checkouts. Custom database and composed host fixtures must use process-local
connections or pytest's worker-local
`tmp_path`/`tmp_path_factory`, and restore registry changes after each test; the
installed app registry is restored for every host loading the base fixtures (see
[Source-addon Test Models](#source-addon-test-models)).

### Source-addon Test Models

Share source-addon compositions through their owning test apps:
[`angee.resources.testing`](../src/angee/resources/testing/__init__.py),
[`angee.workflows.testing`](../src/angee/workflows/testing/__init__.py), and
[`angee.integrate.testing`](../src/angee/integrate/testing/__init__.py). Their
package docstrings own the adoption contract. Other reusable concrete
compositions, including [`decisions`](../src/angee/decisions/testing/models.py)
and [`messaging`](../src/angee/messaging/testing/models.py), register once
through imports in the root conftest before database setup. Tests roll back by
default over a session baseline: the base plugin binds the installed addons' merged
permission inputs (a composed host binds its generated ones), and its
`django_db_setup` synchronizes REBAC once per worker; a host extends that fixture
to load its own baseline rows, and the plugin records every row once the outermost
extension returns. A database test requests pytest-django's native `db` fixture or
carries a `django_db` mark. Declare `django_db(transaction=True)` only when the
contract needs real commits: other threads or connections, row locks, or
`on_commit` work a real worker runs; its flush restores the recorded baseline
rows, keys included, instead of recomputing them. Assert `on_commit` callbacks with pytest-django's
`django_capture_on_commit_callbacks`. A test synchronizes REBAC itself only after
binding its own schema; `composed_permissions` binds a test-owned copy of the merged
inputs for a test that edits one on disk. Register
`angee.base.testing.fixtures` through the root conftest's `pytest_plugins`; a runner
that loads it explicitly must pass `-p django -p angee.base.testing.fixtures` in that
order so pytest-django cannot shadow the shared `django_db_setup` override.
Hosts installing provenance also register
[`angee.provenance.testing.fixtures`](../src/angee/provenance/testing/fixtures.py),
after the base plugin: it adds test attribution and loads every installed addon's
install-tier statement principals (`provenance.Rule` and `Seed` rows and their
grants) into the session baseline.

Declare a test-local model under Django's `isolate_apps` when the code under test
accepts a registry. When its owner reads the installed registry (content types, the
deletion collector, system checks), create the model inside the test on an
installed app label: the autouse `installed_app_registry` fixture in
[`angee.base.testing.fixtures`](../src/angee/base/testing/fixtures.py) returns the installed registry to its
state before each test, so a model the test creates is unregistered, with its
through models, when the test ends, while a model module the test imports first
keeps its models registered. Installed app configs a test changes are put back too.
The plugin's `monkeypatch` fixture composes native `pytest.MonkeyPatch` inside the
registry guard, so temporary modules disappear before restoration; their models
are removed too.

Workflow tests reach execution states through the shared `load_workflow`,
`start_run`, `run_until`, `decide` and `run_factory(...).at(...)` drivers. These compose
production admission and transition verbs; do not fabricate step runs or attempts
to stand in for execution. The scoped `capture_tasks` and `observe(model)`
helpers capture task sends and publications. Declared trigger sources bind
through their native app lifecycle. Decisions-only suites use
`angee.decisions.testing` drivers with central concrete models. Name Python
test modules `test_<concern>.py` and tests
`test_<behavior>` so native discovery and focused file selection agree.

### GraphQL Document Budgets

Classify captured SQL with [`angee.base.testing.queries`](../src/angee/base/testing/queries.py).
Its `statement_startswith` and `is_rebac_revision_read` helpers inspect the outer
statement after CTEs and PostgreSQL cursor declarations; `statement_body` exposes
that body for projection assertions. Each captured entry still counts once.

[`angee.graphql.testing.documents`](../src/angee/graphql/testing/documents.py) executes
one document as one actor, as served requests do, and records the SQL Django captured
on every configured database. The same helpers run an addon schema in the bare source
host under pytest and the composed console schema under Django's test runner:

```python
read = measure_graphql_document(schema, document, variables, actor=member)
assert read.data["projects_aggregate"]["aggregate"]["count"] == 3
read.assert_ceiling(statements=10)
```

A ceiling is the measured statement count; generated SQL text has no size budget,
and its shape is asserted with the classifiers above. A change that lowers a cost
lowers its ceiling in the same change; a rise is a regression to fix, never a reason
to raise the ceiling. Assert the rows a read returns beside its ceiling, so a read
that loses rows cannot pass. The
[console document ceilings](../tests/native_graphql_document_budgets.py) pin the
console app's inbox, transcript, person, projects and current-user reads for a
platform admin and a member, each after one warm execution, in the
[composed examples host](../tests/test_composed_examples.py) on SQLite.

PostgreSQL 17 planner gates use the same shared query-support owner's
`capture_planner_memory`, preserving SQL parameters for `EXPLAIN (MEMORY)` without
`ANALYZE`. Timeline and inbox parts each budget a single planned statement. The
member person/projects documents budget the sum of every SELECT's allocation,
not peak resident memory; their total statement ceilings also count audit writes.
The examples profile runs the member planner gate on PostgreSQL and the console
document statement budgets on SQLite.

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
`tests/test_owner_index.py` validates the owner index citations: every
`` `Symbol` path `` in `.agents/skills/angee-design/references/owners.md` names a
file that declares the symbol, and every public class a foundation models or fields
module declares, and every source distribution (including libraries), has a row. It
also refuses any `path:line` or `path:line-line` in the skills, capability guides
and their references, a guide line in shorthand (`guidelines:120`) and a line
cited in prose (`line 120`); only `docs/` references keep a line.
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
| Install for development | `uv sync --locked` | Installs native editable distributions on a stopped/new host; does not compose |
| Compose models and artifacts | `uv run --no-sync python manage.py compose` | Fresh build-mode process through the native launcher; no database provisioning |
| Install for production | `uv sync --locked --no-editable` | New/stopped host; installs distributions, does not compose |
| Build distribution wheels | `uv build --wheel --all-packages` | Native workspace builds; composition is a separate job |
| Provision database state | `uv run --no-sync python manage.py provision` | Compiler-owned native history checks, migrations, hooks and readiness record |
| Read readiness | `uv run --no-sync python manage.py provision --check` | Read-only proof for the selected database |
| Check composition drift | `uv run --no-sync python manage.py compose --check` | Native read-only artifact/receipt comparison |
| Check migration drift/imports | `uv run --no-sync python manage.py check_migrations --imports` | Native retained-history integrity and Python import audit |
| Author schema migrations | `uv run --no-sync python manage.py makemigrations <app-labels>` | After compose; review and commit `runtime/<label>/migrations/` before packaging |
| Apply migrations | `uv run --no-sync python manage.py migrate` | Operates on the selected stack database |
| Sync permissions | `uv run --no-sync python manage.py rebac sync` | After migrations when permissions change; `rebac sync --prune-stale` also retires removed definitions, the shape provision runs ([REBAC](backend/guidelines.md#rebac)) |
| Load declared resource data | `uv run --no-sync python manage.py resources load` | After migrations when resource data changes |
| Validate runtime contracts | `uv run --no-sync python manage.py check` | Includes construction of every named GraphQL schema; run against deployment code and settings before starting writer tiers |
| Check GraphQL SDL | `uv run --no-sync python manage.py schema --check` | Separate from the composer drift check |

Run only lifecycle steps needed for the change and within the user's authorized
scope.
For containerized stacks, use the declared service/job environment; a host shell
without the runtime dependencies cannot run these commands directly.

The optional Redis channel-layer cross-process test uses
`ANGEE_TEST_CHANNEL_REDIS_URL` to select a disposable Redis database. It skips
when the variable is unset or Redis is unreachable. Use a database separate
from the running stack's channel layer and Celery broker.

After a model change, run native compose and author
required native history before provision. Compose emits GraphQL SDL through its
capability integration. Vite dev and build await shared `@angee/app` generation
automatically; preview serves the built output. Standalone addon fragment checks
do not start Vite, so invoke the framework CLI **before** checking them. From the stack
root, for the default `web_path=web`:

```sh
pnpm --config.verify-deps-before-run=false --dir web exec angee-web-codegen --runtime ../runtime --web-root .
```

Use the rendered `web_path` if customized and adjust `--runtime` relative to it.
Root test/typecheck scripts do not implicitly perform codegen. After a
manifest/dependency change, synchronize at the stack's dependency owner before codegen; never install in the source slot.

## Frontend Packages And Addon Fragments

FormSpec source generation is a repository operation, separate from package builds.
From the framework source root, run `pnpm generate:form-spec` after editing the
canonical `src/angee/base/schemas/form_spec.schema.json`, and verify committed
outputs with `pnpm check:form-spec`. Generator tool versions are exact in
`packages/ui/package.json`; ordinary UI builds consume the committed outputs.

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
Storybook's `catalog:check` verifies catalog and token-name drift, specimen
reachability, guideline story links and the token deletion findings.

For page or addon changes, run a primitive-drift scan and explain every hit
outside `@angee/ui`. A hit is not automatically wrong, but it must either compose
the shared primitive or identify the owning framework gap to fix first. From the
framework source root; include `../angee-messaging-bridges/src` in the scan
when that optional slot is present and affected:

```sh
rg -n '<table\b|<thead\b|<tbody\b|<tr\b|<td\b|<th\b|role="grid"|useReactTable|manualPagination' packages src examples -g '*.tsx'
rg -n 'useAuthored(Query|Mutation)<|interface .*Data|interface .*Variables|fetch\([^)]*graphql|gql`' packages src examples -g '*.ts' -g '*.tsx'
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
pushes to `main`. A pull request's merge gate is the static tier, the SQLite tests
its changed paths reach, the packages and the composed stack with its smoke.
Pushes to `main` and releases run the whole SQLite suite and the PostgreSQL lane,
so whole suites report after a merge instead of gating it. The current tiers are:

- **Static** (the `python-quality` job): Ruff over the source tree, mypy over
  addons, `lint-imports` over the declared import contracts, and the tests that
  read source and docs without Django: the [patch guard](../tests/test_patch_targets.py)
  and the [documentation and methodology tests](#agent-methodology-and-documentation),
  using the locked framework environment.
- **SQLite:** six shards that pytest-split cuts into contiguous chunks of equal
  stored time from the committed [durations](../.test_durations), so each test
  file stays in one or two shards. Each shard runs `-n auto --dist loadfile` with
  a 60-minute limit and prints its 25 slowest test durations, with migrations
  enabled and `DATABASE_URL` unset. The composed-host tests run here, one node per
  composed profile: each composes and boots once and reports every test it ran as
  a subtest, and the heaviest (the examples profile, about 560 stored seconds)
  sets the floor of its shard. On a pull request the shards run only the test
  files [`changed_path_selection.py`](../scripts/changed_path_selection.py) picks
  from the merge commit's diff against its base: changed test files and the test
  files that import a changed module through the import graph `lint-imports`
  checks; a change under `templates/` selects the template contract tests that
  render or read them. A change to an input every test reads without importing it
  (dependencies, conftests and the shared test plugins, settings and autoconfig,
  `*.zed` schemas, resource YAML, the workflows, `scripts/`) selects the whole
  suite; docs and web code select no Python test. What the app
  registry, signals or composition connect without an import surfaces in the whole
  suite on `main`.
- **PostgreSQL:** on `main` and releases, in six groups of its own beside the
  SQLite shards, assigned by pytest-split's `least_duration` algorithm over the
  [PostgreSQL timings](../.test_durations_postgres) and with the shards' 60-minute
  limit, the named PostgreSQL contract selection runs with
  `-n 4 --dist loadfile --nomigrations` and prints its 25
  slowest test durations; each group writes JUnit output and checks that it did
  not skip tests. Its PostgreSQL 17 service includes pgvector and enables `vector`
  in `template1` before any test database is created. Its server runs with
  `max_locks_per_transaction = 256`, since
  each worker creates every table in one transaction. The `uv` job passes only
  when the static tier and every SQLite shard pass and, outside pull requests,
  every PostgreSQL group passes, so callers keep one check name.
- **Packages:** framework package typecheck/test/build and export/distribution
  checks.
- **Composed stack:** compose the host, check generated documents
  and addon fragments, smoke the composed models, and run the bridges repository's
  pytest suite with its
  native source-test settings and collection policy. Bridge tests use the
  composed stack's runtime dependencies plus the framework's locked check tools,
  as the consumer lane does. The tools export omits workspace members already
  supplied by the composed host. Bridge packages declare dependencies in their own
  `pyproject.toml`; native lockfiles own resolved versions. The suite's package
  metadata guard checks that the collection requirements match its workspace
  member distributions.
  The composition smoke catches escapes the bare source host cannot compose. Before
  provision, `makemigrations --check` proves the authored native history reproduces
  the composed models and `schema --check` compares the SDL. After provision, the
  first execution job runs the full `manage.py check --database default`, loads the
  demo tier, and loads one row of every composed model with only its primary key and
  saves it again (append-only models only load).
  Framework composed-host tests create their own temporary hosts in the SQLite
  suite; the stack job does not repeat that framework suite.

A test missing from the durations still runs: pytest-split gives it the average
duration and places it in exactly one shard. Ties in the assignment fall back
to collection order, so an opt-in `-p randomly` run must give every shard one
shared `--randomly-seed`. Refresh both maps after a change that moves test cost,
after changing the PostgreSQL selection, or when a shard nears its limit or its
balance drifts (the shards' printed estimates against their wall times). Record
each map with its CI invocation and pytest-split's store, which sums setup, call
and teardown per test; `--clean-durations` drops the entries of tests the run no
longer collects, which pytest-split would otherwise keep when it merges into the
existing map. For the SQLite suite:

```bash
env -u DATABASE_URL uv run --locked python -m pytest -q -n auto --dist loadfile --store-durations --clean-durations
```

For the PostgreSQL selection, on a server prepared as in
[Local PostgreSQL Prerequisites](#local-postgresql-prerequisites):

```bash
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/angee
pg_tests=$(uv run --locked python - <<'PY'
import shlex
from pathlib import Path

import yaml

steps = yaml.safe_load(Path('.github/workflows/reusable-checks.yml').read_text())['jobs']['uv-postgres']['steps']
run = next(step['run'] for step in steps if step.get('name') == 'Verify PostgreSQL contracts execute without skips')
print(' '.join(arg for arg in shlex.split(run.split('uv run --locked python -c', 1)[0]) if arg.startswith('tests/')))
PY
)
test -n "$pg_tests"
uv run --locked python -m pytest -q -n 4 --dist loadfile --nomigrations --store-durations --clean-durations --durations-path .test_durations_postgres $pg_tests
```

Both maps came from local macOS arm64 runs of these commands (SQLite with
`-n 6`) on a loaded host, the PostgreSQL one against `pgvector/pgvector:pg17`
(PostgreSQL 17.10) with `max_locks_per_transaction = 256`. Use them for relative
balance and check actual CI wall times against the 60-minute limit.

Separate workflows enforce private-path exclusion and other repository policies.
The [release workflow](../.github/workflows/release.yml) runs on pushes to `main`: it
opens the Version Packages pull request from pending changesets once the
repository lets GitHub Actions create pull requests, and it publishes only with
an `NPM_TOKEN` secret ([Changesets](../.changeset/README.md)).
Executable jobs in the reusable workflow own timeouts; GitHub's reusable-workflow
caller syntax does not accept `timeout-minutes`.

Consumer repositories call the same reusable checks with `uv-enabled: false`,
`pnpm-enabled: false`, and `stack-enabled: true`. Their caller owns the explicit
`consumer-sources` (repository, sibling slot path, ref), `consumer-roots`,
`consumer-path`, `consumer-tests`, and optional `consumer-pytest-plugins` inputs.
The caller revision replaces its source ref; dependency revisions remain explicit.
The full declared Python collection runs once across six `stack-shard` jobs,
using native pytest-split `least_duration` assignment. Without stored consumer
timings, the plugin balances test counts with equal weights. Each shard renders
its own host, resolves native distribution requirements, composes/provisions it, and
collects the declared Python paths with `python_files=test*.py` on PostgreSQL.
Four native pytest-xdist workers use `--dist worksteal` inside each shard, so long
consumer intake modules can use every worker. Each worker loads the configured
host through its authored `settings` module; pytest-django owns its separate test
database. The pgvector service enables `vector` in both the existing host database
and `template1` before provisioning, so consumer test databases inherit it with
`--nomigrations`. Pytest emits a thread dump after five minutes in one test protocol,
including setup and teardown, without changing its outcome. Shared test plugins
must defer model imports until Django's app registry is populated. Check tools
come from the framework lockfile.

Group 1 runs consumer Ruff/mypy and, after host SDL/codegen, the consumer's web
fragment typechecks and tests. Mypy resolves composition targets through the
native source roots of all declared `consumer-sources`, including sibling
dependencies. Optional full-profile bridge tests also run only in group 1.
Calls without a consumer use one execution job. The `stack` aggregate
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

During a coordinated draft cutover, publish the reviewed framework commit first.
Consumer callers temporarily pin that public immutable SHA in both the reusable
workflow `uses` reference and `framework-ref`. Pin source dependencies to published,
reviewed cohort commits as well; an unpublished local hash is not a CI dependency.
Run full consumer verification through published CI after the draft cohort
converges; do not recreate its profiles or shards in a private local harness.

After the reviewed framework and source dependencies land, durable consumer
callers use `main` for the reusable workflow, framework checkout, and source
dependencies. In either phase, the caller's own source uses its exact
`github.sha`; its declared source ref is replaced by that revision. Workspace branches do not
define the durable CI dependency contract.

For durable `main` wiring, land in dependency order:

1. Publish required upstream fixes and record their versions or immutable Git
   revisions in the framework dependency declarations and lockfile.
2. Land the framework's reusable workflow and required composition changes on
   `main` before publishing consumer callers that use the new inputs.
3. Land source dependency changes on their `main` branches before publishing
   dependent consumer callers. A caller tests its own review revision against
   those already-landed dependencies.

Local source checks do not establish that this publication chain or a remote CI
run has completed. Billing rejections and private-source checkout failures are
external blockers, not evidence that the consumer checks executed or passed.

Framework requirements pin the native compiler to its public PyPI release,
and `uv.lock` records the distribution artifacts. Clean checkouts and Docker
builds resolve that release without a sibling compiler checkout or named context.

Compiler co-development may use an explicit editable sibling checkout in a
separate disposable check environment. Keep that local override out of committed
requirements/locks and never apply it to a live stack environment. Normal native
installation continues to consume the committed public release pin.
