---
name: angee-runtime
description: "Executes the Angee runtime lifecycle on a stack: native compose after package sync, verified serving, migration authoring/drop guards, provisioning, operator holds/restarts and explicit history retirement/reset. Use for 'build', 'provision', 'migrate', 'restart', 'rebac sync', 'drift'."
---

# Angee Runtime

Use for explicit composition, verified serving, native migration authoring,
provisioning or a generated-artifact failure. Run `angee-design` before changing
an owner. Normal application data changes belong to their addon.

## Owners

| Concern | Owner |
|---|---|
| Build/serving mode and reloader parent | `django_compose.management.execute` |
| Composition snapshot and app closure | `django_compose.conf`, `snapshot`, `components` |
| Models and structural integrations | `django_compose.models`, `model_composition`, `declarations` |
| Artifact publication and serving verification | `django_compose.build`, `artifacts`, `serving` |
| Native history, guarded drops and retirement | `django_compose.migrations` and native management commands |
| Database prep, per-app hooks and readiness | `django_compose.provision` |
| HTTP child and production worker serving | `src/angee/server/management/commands/` |
| Dependency order and held service recovery | Stack `angee.yaml` jobs; native Go operator |

## Work

1. Read the authored project settings and installed package metadata. Preserve
   `COMPOSE_OUTPUT_DIR` / `COMPOSE_OUTPUT_MODULE`, the native snapshot, and normal
   Python importability. Serving consumes receipts; it does not rebuild them.
2. On a stopped/new host, native uv installation precedes `manage.py compose`.
   On a running stack, use its operator dependency chain; never sync its live
   venv. Packaging builds archives only. Commands and working directories live
   in [Checks](../../../docs/checks.md#composition-and-schema).
3. Source participants remain abstract native Django models using `Composable`
   class keywords. Capability build integrations implement the compiler's public
   structural protocols. Keep REBAC/resources/web policy with those addons.
4. Run native `makemigrations` after composition for changed labels, review the
   emitted history and commit it. Author required data operations in ordinary
   project migrations with explicit dependencies and historical Django models.
   An ignored output tree is not a migration retention policy.
5. Retained history survives addon removal. Package-removal preflight audits
   historical Python imports before applying the native lock change. Preserve
   narrow historical aliases where required; never rewrite released migrations
   or invent a second migration interpreter.
6. Use native `check_migrations`, `retire_history` or
   `reset_migration_history` only for their explicit intent. Read the command
   owner before choosing options. Retirement/reset requires applied-history and
   dependent-history proofs; it is not routine deployment repair.
7. Provisioning is a separate compiler-owned database operation. It uses native
   migration checks/execution and idempotent `AppConfig.compose_provision` hooks.
   Writer readiness consumes the recorded provision state; a successful compose
   or permission check alone does not prove readiness.
8. Development `runserver` uses the native launcher/reloader parent; the server
   child runs Uvicorn. Production consumes prepared artifacts. The operator owns
   service starts/restarts; do not spawn development servers manually.

## Verify

Run the finite owner checks needed by the change, then the required integrated
verification. Distinguish distribution build, composition, native history,
database state, schema emission and browser behavior. Preserve passing evidence
for unchanged code and report exact absent native interfaces or integration
prerequisites. Do not patch generated output to make a check pass.

- [Composition owners](../../../docs/composer.md#owner-map)
- [Migration procedures](references/migrations.md)
- [Migration policy](../../../docs/backend/guidelines.md#migration-history)
