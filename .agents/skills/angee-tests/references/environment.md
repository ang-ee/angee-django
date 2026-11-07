# Environment and checks

Pitfalls of the test environment, the shared model registry and the running
stack, moved from the backend guide, plus the verification procedure its Checks
section carried. The contracts stayed there:
[Development server](../../../../docs/backend/guidelines.md#development-server),
[Integration and worker contracts](../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
(TLS trust) and [Naming](../../../../docs/backend/guidelines.md#naming).
Commands live in [Python and composition contracts](../../../../docs/checks.md#python-and-composition-contracts).

## Verification procedure

Run focused tests while editing, the applicable broad checks before handoff,
and report any check that could not run with its blocker. Which command and
execution root each check takes is in
[Checks](../../../../docs/checks.md); what CI covers is in
[What CI actually runs](../../../../docs/checks.md#what-ci-actually-runs).

Before deploying writer tiers, run the default system checks (`manage.py
check`, [Composition and schema](../../../../docs/checks.md#composition-and-schema))
against the deployment's code and settings: the
[schema lifecycle contract](../../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication)
builds every named schema there, and a writer started without that check
builds its schemas lazily on its first observable write.

## Pitfalls

- **Localhost ports do not isolate browser cookies.** Two development stacks on
  different ports replace each other's login cookies unless the session and
  CSRF cookie names differ; the project template's project-scoped names are the
  [development server contract](../../../../docs/backend/guidelines.md#development-server).
- **Run every changed test module standalone.** A full suite's file order can
  leak concrete test models into the shared registry and mask a missing
  registration; a broad run does not replace the direct module run.
- **Temporary app registries can wire shared signals.** Django's `isolate_apps`
  runs app ready hooks. Tests including auth restore the sign-in stamp through
  [IAM's signal owner](../../../../src/angee/iam/signals.py), as the
  [deletion tests](../../../../tests/test_deletion.py) do, so later tests retain the
  configured lifecycle.
- **Django owns static test-table lifecycle.** Register concrete models before
  database setup in installed, unmigrated apps, and use pytest-django's native
  setup and transactional flush. Share source compositions through the owning
  [`resources`](../../../../src/angee/resources/testing/__init__.py),
  [`workflows`](../../../../src/angee/workflows/testing/__init__.py), and
  [`integrate`](../../../../src/angee/integrate/testing/__init__.py) test apps.
  Import the reusable addon-owned concrete compositions from the root test
  conftest before database setup, including
  [`decisions`](../../../../src/angee/decisions/testing/models.py) and
  [`messaging`](../../../../src/angee/messaging/testing/models.py); decisions tests
  import those models without workflow test support.
  Framework probes declared after setup, in isolated registries, unmanaged, or
  under uninstalled or migrated
  labels use the single [`model_tables`](../../../../tests/tables.py) helper. It drops only
  tables it created; it never clears existing tables. Keep production code
  independent of test support.
- **Patch inherited Django manager methods on the manager class.** Pytest's
  `monkeypatch` can restore an instance patch as a bound instance attribute;
  Django's `db_manager()` copies then retain the original manager and lose their
  binding. Patch `type(manager)` and accept the manager argument in the spy.
- **A relocated virtualenv can retain stale launcher shebangs.** Diagnose the
  interpreter and environment owner when a console script cannot spawn; do not
  assume an application failure. [Checks](../../../../docs/checks.md) owns the supported
  commands, module invocation, and environment preparation.
- **Celery workers do not autoreload.** Only the ASGI `runserver` reloads on
  source edits. After changing task, model or settings code, restart the worker
  services through the operator, or run the whole-application restart; see
  [Restart the running stack](../../../../docs/howto/getstarted.md#restart-the-running-stack).
- **Event-based reload needs `pywatchman`.** Without it Django's autoreloader
  polls once a second; install `pywatchman` into the host environment for
  event-based reload.
- **A sandbox without a CA store fails every outbound HTTPS call.** The
  [TLS contract](../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
  keeps trust in the environment; repair the sandbox once at bootstrap with the
  [outbound TLS trust](../../../../docs/checks.md#outbound-tls-trust) recipe,
  never by adding `certifi` plumbing to call sites.
- **Each running stack needs a unique Compose project name and edge port.**
  Colliding names can merge containers into another stack's Compose project;
  colliding ingress ports can route requests to the wrong runtime. Inspect the
  rendered stack manifest and use its template-owned names and port leases.
  A source workspace inside one stack does not itself imply a second running
  stack. Resolve the lifecycle owner through the workspace workflow before
  changing stack or service templates.
- **Addon moves can change the workspace dependency graph and live imports.**
  Use [checks and dependency preparation](../../../../docs/checks.md) to select the owning
  environment. In a materialized workspace, run a needed `pnpm install` only at
  the owning stack root, never in this source slot.

## Seam guards

Before adding a backend abstraction, run the
[owner search](../../../../docs/checks.md#owner-search) scans for the native
owner. If the change introduces or extends a seam, add a focused guard in the owning
test area: layering in
[`tests/test_base_layering.py`](../../../../tests/test_base_layering.py) (dependency
direction) and [`tests/test_layering.py`](../../../../tests/test_layering.py) (import
closures), addon/manifest binding in app tests, settings/autoconfig/app graph
behavior in [`tests/test_settings.py`](../../../../tests/test_settings.py), runtime
emission in [`tests/test_compose.py`](../../../../tests/test_compose.py), migration
history in [`tests/test_runtime_migrations.py`](../../../../tests/test_runtime_migrations.py),
and schema composition in GraphQL tests. Preserve the focused proof obligations
in the relevant pitfalls: isolated test-module runs, non-admin authorization
coverage, narrow GraphQL selections at multiple row counts, and populated-state
data-migration checks.
