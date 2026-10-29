---
name: angee-runtime
description: "Executes the Angee runtime lifecycle on a stack: build (uv sync Hatch hook), makemigrations with the drop guard, provision (migrate, rebac sync, check), operator restarts and dependency changes, upgrade order, clean/reset. Use for 'build', 'provision', 'migrate', 'restart', 'rebac sync', 'drift'."
---

# Angee Runtime

This skill owns **execution** of the lifecycle on a stack. `angee-project` owns
host selection and settings (`settings.yaml`, `INSTALLED_APPS`, stack profile);
`angee-addon` owns an addon's local declarations (`addon.toml`, `[[migrations]]`,
`retired_models`); `angee-permissions` step 7 owns the REBAC sync decision.
Every command runs from the stack root through its host.

## Use when

- "Build / rebuild the runtime", "`angee check` says the runtime is stale".
- "Make / apply migrations", "`makemigrations` refuses a drop", "data migration".
- "Provision the stack", "restart django / the workers", "apply a dependency change".
- "`rebac sync` fails", "`rebac.E009`", "the sync is deadlocked".
- "Clean the runtime", "reset migration history", "upgrade across the cutoff".

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `angee/hatch.py` (`RuntimeBuildHook`, `build_runtime`, `_compile`) | The build: editable `uv sync` compiles the runtime in a fresh interpreter without a history check (`:330`); `uv sync --no-editable` and `uv build` compile with `check_history` (`:340`), which refuses missing, foreign or drifted tracked history; the worker authors nothing | `angee/hatch.py:318,242,305`; history validation `:272-299` |
| `angee/build.py` `ProjectBuildConfig`, `angee/compose/apps.py` `BuildComposeConfig` | Explicit build-time compilation; default `MIGRATION_MODULES[label]` = `<runtime_module>.<label>.migrations`, i.e. `runtime/<label>/migrations/` | `angee/build.py:76,82`, `angee/compose/apps.py:14,32` |
| `angee/apps.py` `AngeeConfig` | Serving import of the prebuilt runtime with app-identity and migration-mapping validation | `angee/apps.py:12,19` |
| `angee/compose/runtime.py` `Runtime` | Render, emit, drift check and guarded clean of `runtime/`; `GENERATED_SENTINEL`; `labels` | `angee/compose/runtime.py:22,130,151,225,245` |
| `angee/utils/artifacts.py` `GeneratedTree` | Root and marker guard before any write or clean | `angee/utils/artifacts.py:13,111` |
| `angee/management/commands/angee.py` | `angee check`, `angee clean`, `angee provision` and its flags | `angee/management/commands/angee.py:29,32,35` |
| `angee/management/provision.py` | The provision plan and order, database wait, `ANALYZE` after migrate, `--after-if-installed`; it checks migration authoring and refuses drift | `angee/management/provision.py:49,62,95,140` |
| `angee/management/commands/{makemigrations,reset_migration_history}.py`, `angee/compose/migrations.py` | Explicit authoring dispatch, addon transition binding, `DropGuardAutodetector`, `reset_migration_history`; native Django owns `migrate` | Host dispatch `angee/management/commands/makemigrations.py:8`, `angee/management/commands/reset_migration_history.py:8`; build authoring `angee/compose/management/commands/makemigrations.py:29`; policy `angee/compose/migrations.py:239,638,79` |
| Stack `angee.yaml` jobs (rendered from the template) | `deps` (framework stacks) → `runtime-build` (`uv sync`; `--no-editable` under the production serve mode) → `provision` → `operator-schema` → `codegen` (framework stacks; instance stacks run codegen inside `frontend-build`); the restart job; the operator stops the chain on a failed job | `templates/stacks/_shared/stack-body.yaml.jinja:232,237,255,258,262,285,325` |
| The operator (`angee --root …`, its GraphQL API) | Whole-application and single-service restarts | `templates/stacks/_shared/AGENTS.md.jinja:38`, [restart](../../../docs/howto/getstarted.md#restart-the-running-stack) |
| Installed `rebac/checks.py` `check_field_backed_relations` | `rebac.E009`: a zed relation's field/attribute/const backing must resolve to a model and canonical identity | `rebac/checks.py:298` |

## Reuse first

- The verbs are native: `uv sync` / `uv build` (Hatch hook), Django `makemigrations`
  / `migrate` / `check`, `rebac sync`, `resources load`, `schema`; `angee provision`
  orders them and stack jobs call it. Scripting the chain again duplicates `provision.plan()`.
- The operator owns every restart; `angee dev` owns starting the stack
  ([Run From The Root](../../../AGENTS.md#run-from-the-root)).
- `Runtime.check()` owns drift, `Runtime.clean_configured()` cleanup,
  `reset_migration_history` history replacement, `DropGuardAutodetector` the
  "still holds data" proof; a declared addon cutover or `retired_models` owns
  the reviewed removal (`angee-addon`).
- [Composition And Schema](../../../docs/checks.md#composition-and-schema) owns
  the command table and execution root; this skill names one recipe per step and
  links that table for the rest.
- `angee-permissions` step 7 owns which `rebac sync` shape a stack needs.

## Steps

1. **Resolve the stack root and decide the mode.** Resolve `ANGEE_ROOT` with
   `angee-workspace` and `cd` there. If the stack is stopped → run steps 2–4 there
   as one-shot commands. If it runs → builds, dependency changes and migration
   *application* go through the restart job (step 7); migration *authoring*
   (step 3), `angee check`, `schema` and the other one-shot commands still run
   from the root in a fresh `uv run --no-sync` process, which cannot rebuild
   ([Composition And Schema](../../../docs/checks.md#composition-and-schema)).
   **Owner rule:** [Run From The Root](../../../AGENTS.md#run-from-the-root) —
   one-shot commands use the stack host's `manage.py`; a source slot is not a host.

2. **Build.** Native installation is the build; the serve mode picks the hook path:

   ```sh
   uv sync                # development: editable runtime/ + sentinel, no history check
   uv sync --no-editable  # deployment: validates tracked history, installs the sealed wheel
   ```

   Both run `angee.build.ProjectBuildConfig` in a fresh interpreter and open no database. The
   editable build (`angee/hatch.py:330`) emits `runtime/` with the sentinel in
   `__init__.py`. The `--no-editable` install and `uv build` (`:340`) first refuse
   a missing, foreign or drifted `runtime/<label>/migrations/` (`:272-299`): a
   deployment consumes history already authored and reviewed on an editable
   build (step 3). `angee check` (same table) reports stale sources or pending
   addon declarations without writing; "Cannot import prebuilt runtime" or
   "Installed Django apps differ" → `uv sync` again.
   **Owner rule:** the composer owns emission and startup does not repair output
   ([Flow](../../../docs/composer.md#flow), [Invariants](../../../docs/composer.md#invariants));
   an edit under `runtime/` outside `*/migrations/` is lost at the next build, so
   the change belongs in addon source.

3. **Author migrations on an editable build, in a fresh process.**

   ```sh
   uv run --no-sync python manage.py makemigrations <changed-labels>
   ```

   Declared addon `[[migrations]]` bind to the host graph first, then Django
   autodetects the rest. Output lands in `MIGRATION_MODULES[label]`, which the
   host build defaults to `<runtime_module>.<label>.migrations` — the directory
   `runtime/<label>/migrations/` beside the generated models (`angee/build.py:82`);
   an explicit mapping in `settings.yaml` wins and `None` disables the app. The
   rendered `.gitignore` keeps exactly that path (`templates/stacks/dev/template/.gitignore.jinja:11`):
   review and commit it, because `uv sync --no-editable` and `uv build` refuse a
   missing or drifted history (`angee/hatch.py:340`, `:272-299`). Drop guard refuses a `RemoveField`/`DeleteModel`
   → the owning addon declares a cutover or `retired_models` (`angee-addon`), then
   rerun; an empty column needs nothing. A restricted run lists every changed
   concrete label (`Runtime.labels`, `angee/compose/runtime.py:151`). Keep original
   chains while a database may still be partially upgraded.
   **Owner rule:** [Addon-owned runtime migrations](../../../docs/composer.md#addon-owned-runtime-migrations)
   owns binding, immutability and the module mapping; Django owns detection,
   naming and writing. The build worker authors nothing (`angee/hatch.py:245`).

4. **Provision (stack not running).**

   ```sh
   uv run --no-sync python manage.py angee provision --after-if-installed 'resources load --include-demo' --after-if-installed schema
   ```

   One process, after the database answers: `makemigrations --check --dry-run`
   (`provision.py:62`) refuses pending transitions or model drift until step 3
   ran — provision checks authoring, it does not author (composer.md:386-393);
   then `migrate` (plus `ANALYZE` of migrated tables on PostgreSQL), the sync step
   (step 5), `check --database default`, and each `--after-if-installed` command
   Django's registry contains. Then codegen where the stack runs it: the `codegen`
   job on framework stacks, inside `frontend-build` on instance stacks
   (`stack-body.yaml.jinja:285,325`; [frontend checks](../../../docs/checks.md#frontend-packages-and-addon-fragments)).
   **Owner rule:** `provision.plan()` (`angee/management/provision.py:49`) owns
   the order; each native command keeps its own transaction. Jobs pass flags to
   it; restating its steps in a job duplicates the plan.

5. **Leave the sync decision to its owner.** Provision schedules
   `rebac --skip-checks sync --prune-stale --yes` after `migrate` on every run and
   appends `--force-overwrite` only under `--force-rebac` (`provision.py:58-60`;
   framework-stack jobs pass it, `stack-body.yaml.jinja:267`). Which shape a live
   stack needs — ordinary sync, retirement, deliberate overwrite, `rebac.E009`
   recovery — is `angee-permissions` step 7; this skill runs what it decides.
   **Owner rule:** [REBAC](../../../docs/backend/guidelines.md#rebac) (lines 813-830)
   owns sync semantics; sync, prune and overwrite are the library's options;
   provision owns only their position in the plan.

6. **Dependency changes.** Edit the owning manifest (host `pyproject.toml`, addon
   `addon.toml` `dependencies`) and the lockfile; the Hatch metadata hook projects
   addon requirements. Apply with the whole-application restart (step 7), not with
   `uv sync`/`uv lock` under a running stack, where long-lived workers import a
   mix of old and new modules.
   **Owner rule:** [Composed addon dependencies](../../../docs/composer.md#composed-addon-dependencies)
   owns projection; the operator owns applying it.

7. **Running stack: restart through the operator.**

   ```sh
   angee --root "$ANGEE_ROOT" job run deps --chained-restart   # dev stacks; instance stacks: runtime-build
   ```

   The restart job is `ANGEE_OPERATOR_RESTART_JOB` in the stack's `angee.yaml`
   (`stack-body.yaml.jinja:232`); it re-runs `runtime-build` → `provision` →
   `operator-schema` (→ `codegen` on framework stacks), then restarts the
   dependent services while Postgres, Redis and the operator stay up; `angee
   --root "$ANGEE_ROOT" restart <service>` restarts one. Django autoreloads on
   source edits; workers do not. A model change takes one of two paths; the
   operator stops the chain at the first failed job (`stack-body.yaml.jinja:237`):
   - If the stack installs editable (`uv sync`, `:257-258`) → run the job once:
     `runtime-build` rebuilds and `provision` stops at the drift check while
     history is missing (`provision.py:62`); author with step 3 from the root
     against the rebuilt runtime, review, commit, then run the job again:
     provision migrates and the services restart.
   - If the stack installs `--no-editable` (production serve mode, `templates/stacks/_shared/stack-body.yaml.jinja:231,256`)
     → `runtime-build` itself refuses missing or drifted history
     (`angee/hatch.py:340`, `:272-299`) and provision never starts. Author
     nothing there: deploy a revision whose committed history passed an editable
     build and review, then run the job.
   **Owner rule:** [Restart the running stack](../../../docs/howto/getstarted.md#restart-the-running-stack)
   — a process started outside the operator is one it can no longer manage, so
   while the stack runs the operator owns every build and restart.

8. **Destructive migrations, history reset, upgrade cutoff.** Before a cutover
   that drops or rewrites data, a `reset_migration_history`, or a cutoff upgrade,
   back up the database (`pg_dump` on PostgreSQL stacks) and the runtime with its
   `migrations/`. Order: snapshots → apply pending migrations on the old code →
   canonicalize stored values → `reset_migration_history --database <alias>` only on
   a fully applied, model-clean graph → advance framework and addon pins together →
   `uv sync`, provision, checks, schema; verify fresh and populated installs.
   Publish upstream-first ([Consumer CI Publication](../../../docs/checks.md#consumer-ci-publication)).
   **Owner rule:** [the upgrade cutoff](../../../docs/composer.md#foundation-and-provenance-upgrade-cutoff)
   owns the backup and the order; reset is an explicit cutoff, not a deployment
   step ([Addon-owned runtime migrations](../../../docs/composer.md#addon-owned-runtime-migrations), "`reset_migration_history` is an exceptional, explicit upgrade cutoff").

9. **Clean.** `uv run --no-sync python manage.py angee clean` deletes only the
   configured runtime after verifying the sentinel and keeps `migrations/`; rebuild with `uv sync`.
   **Owner rule:** [Mechanical Overrides](../../../AGENTS.md#mechanical-overrides)
   and [Runtime cleanup](../../../docs/composer.md#runtime-cleanup) — a manual
   `rm -rf runtime/` also deletes the history under `runtime/<label>/migrations/`
   that the override preserves; history leaves only through `reset_migration_history`.

<details><summary>Changes under the build/host plan</summary>

The build moves to hatch-angee and runs without Django (model markers come from
`addon.toml`); provision becomes `check` → `makemigrations` (drop guard) →
`migrate` → `rebac sync` → resources → SDL/metadata in one plain Django process;
`angee/compose` is deleted while `DropGuardAutodetector` and `reset_migration_history`
move to `angee.base`, which owns the host; stack jobs call the hatch-angee build.
Steps 2–4 change command owners; steps 7 and 8 do not.
</details>

## Do not

- Hand-edit or commit generated `runtime/` modules outside `runtime/<label>/migrations/` → the composer re-emits them on `uv sync` ([Invariants](../../../docs/composer.md#invariants); `templates/stacks/dev/template/.gitignore.jinja:11` keeps only history).
- `uv sync`/`uv lock` on the stack venv while it runs → the whole-application restart owns the install while workers are alive ([restart](../../../docs/howto/getstarted.md#restart-the-running-stack)).
- Start Django, Vite, workers or `uvicorn --reload` by hand, or kill a process → `angee dev` and the operator own processes it must later restart ([Run From The Root](../../../AGENTS.md#run-from-the-root)).
- Route migration authoring through the restart job or a deployment → provision refuses drift and authors nothing (`provision.py:62`) and a `--no-editable` build refuses missing history (`angee/hatch.py:340`); author from the root of an editable stack, step 3 ([Addon-owned runtime migrations](../../../docs/composer.md#addon-owned-runtime-migrations)).
- Restate the provision chain in a job, Makefile or script → `angee provision` plus `--after-if-installed` owns the order (`provision.py:49`, [Runtime Build And Import](../../../docs/composer.md#runtime-build-and-import)).
- `--fake`, `--fake-initial`, deleting rows or editing a released migration to pass the drop guard → a declared cutover / `retired_models` owns the reviewed removal ([Addon-owned runtime migrations](../../../docs/composer.md#addon-owned-runtime-migrations)).
- Delete `*/migrations/`, a generated migration or an original chain while a database may still need it → `reset_migration_history` under a cutoff ([Mechanical Overrides](../../../AGENTS.md#mechanical-overrides)).
- Choose a `rebac sync` shape here, `--force-overwrite` included → `angee-permissions` step 7 owns the decision; provision schedules it (`provision.py:58-60`, [REBAC](../../../docs/backend/guidelines.md#rebac) lines 825-830).
- Silence `check` to serve past `rebac.E009` → the model or zed backing owns the fix (`rebac/checks.py:298`; `angee-permissions` step 7 recovery).
- Hand-write `runtime/schemas/*.graphql`, or run `makemigrations` inside the build process → `schema` and the codegen job; a fresh `uv run --no-sync` process (`angee/hatch.py:245`, [Migrations and runtime](references/migrations.md)).

## Verify

- Build: `angee check: ok` and the sentinel in `runtime/__init__.py`; `uv sync --no-editable`
  and `uv build` validate history offline (`angee/hatch.py:340`, `:272-299`). Provision: `angee provision: ok` after `check --database default`;
  `tests/test_provision.py:100` fixes order and single process; the drop guard is `tests/test_provision.py:201`.
- Schema: `schema --check`, then codegen before fragment checks (the `codegen` job on framework
  stacks, `frontend-build` on instance stacks); on a running stack the job completes, services
  return healthy and `runtime/schemas/*.graphql` exist ([Composition And Schema](../../../docs/checks.md#composition-and-schema)).
- False greens: `uv run` without `--no-sync` may rebuild before `angee check`;
  a fresh-database `migrate` skips a row-dependent backfill body; `applies()`
  returning `False` proves nothing; a `--skip-checks` run has not been checked.
- Lane vs integrated: a jj lane has no host or database — run focused
  `tests/test_provision.py`, `tests/test_compose.py`, `tests/test_runtime_migrations.py`,
  ruff and mypy on touched files; build, provision, restart, SDL and codegen run
  only on the integrated src stack ([What CI Actually Runs](../../../docs/checks.md#what-ci-actually-runs)).
  Build-review-verify loop: `.agents/skills/slice/SKILL.md`.

## Pitfalls

- Celery workers do not autoreload; restart them through the operator after task, model or settings edits ([Environment and checks](../angee-tests/references/environment.md)).
- A missing `runtime/schemas/*.graphql` makes the SPA fail silently while `:5173` still answers 200 ([Migrations and runtime](references/migrations.md)).
- Data migrations read REBAC-scoped models through `model._base_manager.using(db)`; `objects` raises `MissingActorError` ([Migrations and runtime](references/migrations.md)).
- PostgreSQL: a manual `migrate` needs `ANALYZE` of changed tables; row updates followed by DDL need `SET CONSTRAINTS ALL IMMEDIATE` ([Migrations and runtime](references/migrations.md)).
- A restricted `makemigrations` must cover every changed concrete app ([Migrations and runtime](references/migrations.md)).

## Read next

- [Migrations and runtime](references/migrations.md)
- [Flow](../../../docs/composer.md#flow), [Runtime Build And Import](../../../docs/composer.md#runtime-build-and-import), [Runtime cleanup](../../../docs/composer.md#runtime-cleanup)
- [Composition And Schema](../../../docs/checks.md#composition-and-schema), [Migration history](../../../docs/backend/guidelines.md#migration-history), [REBAC](../../../docs/backend/guidelines.md#rebac)
- [Restart the running stack](../../../docs/howto/getstarted.md#restart-the-running-stack)
- Skills: `angee-project`, `angee-addon`, `angee-permissions` (step 7), `angee-workspace`, `slice`

<!-- Sources
Docs: AGENTS.md #common-errors, #mechanical-overrides, #run-from-the-root; docs/guidelines.md; docs/composer.md #flow, #runtime-build-and-import, #addon-owned-runtime-migrations, #foundation-and-provenance-upgrade-cutoff, #composed-addon-dependencies, #runtime-cleanup, #addon-declarations, #invariants; docs/backend/guidelines.md #package-layering, #rebac, #pitfalls, #environment-and-checks, #migrations-and-runtime, #persistence-primitives;
docs/checks.md #choose-the-working-directory, #python-and-composition-contracts, #composition-and-schema, #frontend-packages-and-addon-fragments, #browser-verification, #what-ci-actually-runs; docs/howto/getstarted.md #restart-the-running-stack, #whats-needed-for-agents-to-self-build; docs/stack.md #tooling; templates/stacks/_shared/{AGENTS.md,stack-body.yaml}.jinja;
templates/projects/web/template/pyproject.toml.jinja; templates/{projects/web,stacks/dev,stacks/local}/template/.gitignore.jinja. Code: angee/management/
{commands/{angee,makemigrations,reset_migration_history}.py,provision.py,__init__.py}; angee/hatch.py:24-90,242-340; angee/apps.py; angee/build.py; angee/compose/{apps.py,runtime.py,migrations.py:638-645,
management/commands/{makemigrations,reset_migration_history}.py}; angee/utils/artifacts.py:13-122; angee/project.py; installed rebac/checks.py:285-335; tests/test_provision.py:95-130,201.
Doc gaps: no doc names `pg_dump` (docs/composer.md#foundation-and-provenance-upgrade-cutoff step 1 owns the backup) or `migrate --check`; step 8's
backup command is derived from the PostgreSQL stack, not a doc.
-->
