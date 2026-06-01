# The Composer

The composer is the build-time tool that turns addon contracts into a runnable
project (see **Composer** in `docs/glossary.md`). This page teaches *how* it
hooks Django and *why* it emits source. The concrete contract lives in the code
it points to — read those owners; this doc does not restate their API.

## Emit-then-adopt, one boot

There is a single app set and a single boot — no `ANGEE_BUILD` flag, no
build/run split. The composer rides Django's app-loading lifecycle
(`django.apps.registry.Apps.populate`) rather than a separate build phase. Four
hooks, in order:

1. **`INSTALLED_APPS` ordering** — `compose_defaults` lists `COMPOSE_APP` before
   the base and source addons (`angee/base/settings.py`, `_installed_apps`). The
   generated `runtime.<label>` packages are *not* separate installed apps; each
   source addon owns its label.
2. **Emit** — `ComposeConfig.import_models` runs first in app-populate **phase 2**
   and calls `AngeeRuntime.from_settings().emit_if_stale()`, writing
   `runtime/<label>/models.py` for every discovered addon
   (`angee/compose/apps.py`, `angee/compose/runtime.py`).
3. **Adopt** — each source addon's `BaseAddonConfig.import_models` runs later in
   the same phase-2 loop. After importing its own abstract `models.py` (which
   registers no table), it imports `runtime.<label>.models`. The generated
   concrete classes declare `app_label = "<label>"`, so they register under the
   source addon's label (`angee/base/apps.py`). The addon *lends its label* to
   the emitted models.
4. **Migrations redirect** — `MIGRATION_MODULES` maps each label to
   `runtime.<label>.migrations` (`angee/base/settings.py`, `_migration_modules`),
   so Django stores and reads that app's migrations inside the generated tree.

By phase 3 (`ready()`) every concrete model — including `iam.User` — is
registered, so Django's auth and REBAC contracts resolve normally.

## Why emit at all

Most Django plugin systems compose **without** emitting: they concatenate
`INSTALLED_APPS`, mutate settings, append URL patterns, or merge schema objects —
all runtime-native Python values rebuilt each boot. None compose **ORM models**
across addons. Each of their plugins ships a hand-written `models.py`; migrations
are ordinary because every model has exactly one owning app. (A mechanism survey
is in `.agents/notes/composer-extraction/prior-art-comparison.md`.)

Angee emits because it does the thing they avoid: `extends` lets one addon add
fields to another addon's model. The resulting concrete class is assembled from
several addons' abstract bases and is owned by **none** of them, so it must live
in a real module that neither addon ships. Two honest options exist — a human
hand-writes and maintains that join file (django-oscar's `oscar_fork_app`), or
the composer generates it. Angee generates it, deterministically, with field
collisions rejected at build (`AngeeRuntime._check_field_collisions`).

Django requires a concrete model class in the app registry plus a migrations
module; neither approach escapes that. Emission is simply how Angee *produces a
normal Django app* for many composed contributors.

## Load-bearing emission vs. artifacts

Not everything written under `runtime/` is needed to run:

- **Concrete models — load-bearing.** The running process imports the generated
  `models.py`; `makemigrations`/`migrate` diff it. Required, because of `extends`.
- **GraphQL SDL and `permissions.zed` — artifacts.** The GraphQL schema is
  assembled in memory from each addon's `schema.py` at serve time; the `.graphql`
  file is exported for `schema --check`, codegen, and tooling (the
  `schemas/` tree is excluded from model drift). Permissions are emitted for
  `rebac sync`. These are written by deliberate choice — for a diffable,
  reviewable, checkable record — not because the app couldn't compose them live.

## Determinism, the sentinel, and drift

Emitted output is byte-deterministic: addons are topologically ordered by
`depends_on` with a name tie-break, and every collection (labels, extensions,
imports) is sorted before rendering. No wall-clock, random, or filesystem order
enters an artifact.

Generated files carry `GENERATED_SENTINEL` in `runtime/__init__.py`. Destructive
cleanup (`reset`/`clean`) refuses to run unless it finds that sentinel (or a
migrations-only tree), and always preserves `*/migrations/`. `emit_if_stale`
heals drift file-by-file on boot without ever cleaning, so a corrupt or
non-Angee directory can never abort app population; the explicit `angee build`
(`emit`) is the only pass that prunes orphaned labels.

## Working with the runtime

- **Change the source, not the artifact.** `runtime/`, emitted schemas, and
  codegen stubs are output. Edit the abstract source model or the addon
  contract; never hand-edit generated files.
- **Reach concrete models through the app registry** —
  `apps.get_model("base", "Resource")` — never by importing the `runtime/` tree
  (`docs/backend/guidelines.md` → Package Layering).
- **Migrations are real and committed.** They live under `runtime/<label>/
  migrations/`, evolve through `makemigrations`, and survive re-emission.

## Owners

- Emit hook and ordering rationale: `src/angee/compose/apps.py`.
- Rendering, emission, drift, cleanup: `src/angee/compose/runtime.py`
  (`AngeeRuntime`).
- Adopt hook and addon manifest: `src/angee/base/apps.py` (`BaseAddonConfig`).
- App set, `MIGRATION_MODULES`, settings defaults: `src/angee/base/settings.py`
  (`compose_defaults`).
- Discovery and ordering: `src/angee/base/discovery.py`.
- Vocabulary: `docs/glossary.md`. Layering rules: `docs/backend/guidelines.md`.
