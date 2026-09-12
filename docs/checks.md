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
| Python/addon/template handoff; required for core changes | `uv run --locked python -m pytest -q` |
| Python lint | `uv run --locked python -m ruff check . --no-cache` |
| Python types | `uv run --locked python -m mypy angee addons` |
| Dead-code review | `uv run --locked python -m vulture` |

PostgreSQL concurrency behavior also needs the database-backed lane in
[reusable checks](../.github/workflows/reusable-checks.yml). SQLite results do not
substitute for that coverage; report database-dependent skips explicitly.

## Agent Methodology And Documentation

For changes limited to instructions, tool adapters, or their tests, run from the
**framework source root**:

```sh
uv run --locked python .agents/tools/sync-adapters.py --check
uv run --locked python -m pytest -p no:django --noconftest -c /dev/null --rootdir=. -q tests/test_agent_stack_root_policy.py tests/test_agent_work_state_policy.py tests/test_agent_methodology.py tests/test_documentation_contracts.py
git diff --check
```

These tests exercise documentation links/commands, template input agreement,
adapter parity, and mocked helper behavior without starting the application.
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

| Purpose | Command | Prerequisite / effect |
|---|---|---|
| Build composed runtime | `uv run manage.py angee build` | Updates generated runtime and host dependency declarations and materializes pending addon migrations |
| Check composition drift | `uv run --locked manage.py angee build --check` | Checks composer-owned artifacts/dependency projection/migration history; Django bootstrap can repair runtime sources before dispatch |
| Author schema migrations | `uv run manage.py makemigrations <app-labels>` | After composition; preserves existing migration history |
| Apply migrations | `uv run manage.py migrate` | Operates on the selected stack database |
| Sync permissions | `uv run manage.py rebac sync` | After migrations when permissions change |
| Load declared resource data | `uv run manage.py resources load` | After migrations when resource data changes |
| Validate runtime contracts | `uv run --locked manage.py check` | Includes construction of every named GraphQL schema; run against deployment code and settings before starting writer tiers |
| Emit GraphQL SDL | `uv run manage.py schema` | A fresh process loads the emitted concrete models |
| Check GraphQL SDL | `uv run --locked manage.py schema --check` | Separate from the composer drift check |

Run only lifecycle steps needed for the change and within the user's authorized
scope. See [migration policy](backend/guidelines.md) before changing applied
history. A rebuild is not permission to delete migrations or reset a database.
For containerized stacks, use the declared service/job environment; a host shell
without the runtime dependencies cannot run these commands directly.

After a schema change, emit runtime sources as needed, emit SDL, then run the
host web package's codegen **before** checking addon fragments. From the stack
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

| Scope | Working directory | Command |
|---|---|---|
| Focused package test/typecheck/build | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter <package-name> run <script>` |
| Framework package types | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' run typecheck` |
| Framework package tests | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' run test` |
| Published package builds | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match -r --filter './packages/**' --filter '!@angee/storybook' run build` |
| Distribution import checks | Framework source root, after builds | `node packages/scripts/check-dist-imports.mjs` |
| UI export checks | Framework source root, after builds | `node packages/ui/scripts/check-views-exports.mjs` |
| Package/exports architecture | Framework source root | `pnpm --config.verify-deps-before-run=false --fail-if-no-match --filter @angee/app exec vitest run src/architecture-guardrails.test.ts` |
| All composed fragment types | Stack root, after SDL/codegen | `pnpm --config.verify-deps-before-run=false -r run typecheck` |
| All composed fragment tests | Stack root, after SDL/codegen | `pnpm --config.verify-deps-before-run=false -r run test` |

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
[reusable checks](../.github/workflows/reusable-checks.yml): the Python suite,
PostgreSQL concurrency tests, framework package typecheck/test/build and
export/distribution checks, then a composed-stack lane for generated-document
and addon-fragment checks. Separate workflows enforce private-path exclusion and
other repository policies.

Browser e2e is manual; neither `pnpm -r test` nor the current GitHub workflows run
it. Python lint, mypy, and vulture are prescribed local checks but are not jobs in
that reusable workflow. State local evidence and CI evidence separately. When
changing CI, reconcile this section and link to the actual job instead of
promising planned coverage as though it already exists.
