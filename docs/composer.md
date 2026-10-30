# The Composer

`django-compose` owns generic Django composition: component discovery,
settings order, abstract source declarations, generated models and artifacts,
verified serving imports, native migration authoring, and explicit provisioning.
Angee composes those contracts through its addons. There is no separate framework core package.

This guide routes intent to owners. Exact signatures and validation belong to
the linked source and the installed `django_compose` modules.

## Flow

1. **Install distributions.** Native `uv sync --locked` resolves project and
   per-addon requirements. `uv build` builds archives. Neither composes a host.
2. **Load authored settings.** The project's `settings.py` calls
   [`angee.base.conf.configure`](../src/angee/base/conf/__init__.py).
   It loads deployment inputs and delegates once to `django_compose.conf.configure`.
   The compiler derives the enabled closure from the project's `INSTALLED_APPS`.
3. **Compose explicitly.** The rendered `manage.py` calls
   `django_compose.management.execute`. `python manage.py compose` selects build
   mode before settings or models load, then emits the concrete model tree,
   receipt, and declared capability artifacts. No database is provisioned.
4. **Consume verified output.** Ordinary management and serving processes use
   the authored settings module. Native serving verification checks the receipt
   before importing emitted models; startup does not repair missing output.
5. **Provision separately.** `python manage.py provision` applies database
   state through `django_compose.provision`.
   The compiler invokes each enabled app's optional idempotent `AppConfig.compose_provision` hook
   after migrations. The operator's job graph orders dependency sync, compose,
   provision and service recovery. [Checks](checks.md#composition-and-schema) owns the commands.

Development `runserver` uses the native compiler launcher to compose in a fresh
process before each reloader child. Django owns the reload loop; Angee's
[`server command`](../src/angee/server/management/commands/runserver.py)
serves the child with Uvicorn. Production serving consumes prepared output.

## Provision Ownership Upgrade

When upgrading a stack that still has the former `base_provision` completion
table, stop its serving processes and old provisioners through the operator,
install the pinned compiler, and compose the new receipt. Run the host's native
`python manage.py provision --upgrade-from base_provision` once before restoring
service. The compiler validates and invalidates the old completion record before
renaming its table; application data and Django migration history are preserved.
Normal provisioning and readiness use only `django_compose_provision` afterward.
Do not keep an old provisioner running during the cutover.

Custom integrations consume `django_compose.components` and
`COMPOSE_COMPONENT_GROUP`. Angee supplies the `angee.addons` entry-point group;
`AppConfig.compose_provision` remains the optional app-owned preparation hook.

## Owner Map

| Concern | Owner |
|---|---|
| Distribution requirements, entry points, editable installs and locks | Native Python metadata, uv and Hatchling |
| Component providers, requirement closure and Django app identity | `django_compose.components` and Django `AppConfig` |
| Deployment YAML, `.env`, service URLs and Angee defaults | [`angee.base.conf`](../src/angee/base/conf/__init__.py) and [`base.autoconfig`](../src/angee/base/autoconfig.py) |
| Settings merge, structural provenance and immutable snapshot | `django_compose.conf` and `django_compose.snapshot` |
| Angee package data and optional modules | [`angee.base.addons`](../src/angee/base/addons.py) reads packaged `[tool.angee.*]` tables through the native installed index and discovers optional addon modules; capability owners interpret their declarations |
| Source declarations, model composition and collision checks | `django_compose.models.Composable`, `model_composition`, and Django |
| Build/check publication, artifact receipt and serving verification | `django_compose.build`, `artifacts` and `serving` |
| Migration binding, drop guards and retained history | `django_compose.migrations` and native Django migration commands |
| REBAC projection and additive schema fragments | [`PermissionIntegration`](../src/angee/base/build.py) |
| Source-local catalogue facts and resource retirement exemptions | [`CatalogueIntegration`](../src/angee/resources/build.py) |
| Web manifest and Tailwind projection | [`WebIntegration`](../src/angee/web/build.py) |
| Database readiness and optional app preparation hooks | `django_compose.provision` |
| Explicit URL and ASGI contributions | `django_compose.urls` and `django_compose.asgi` |
| HTTP defaults, Uvicorn commands and connection policy | [`angee.server`](../src/angee/server/) |
| GraphQL schema, SDL and WebSocket policy | [`schema.py`](../src/angee/graphql/schema.py) resolves and validates schema declarations; [`asgi.py`](../src/angee/graphql/asgi.py) validates WebSocket contributions; ready-phase [`SchemaIntegration`](../src/angee/graphql/sdl.py) emits SDL/metadata during compose |
| MCP tools, HTTP mount and lifespan | [`angee.mcp`](../src/angee/mcp/) |
| In-place addon layout and metadata defaults | Published [hatch-angee](https://github.com/ang-ee/hatch-angee) backend |

## Settings Bootstrap

The project owns `settings.py` and `settings.yaml`; settings are not generated.
`angee.base.conf.configure(globals())` uses the calling module's directory as
its source. `ANGEE_PROJECT_DIR` may select a deployment directory. Source YAML,
deployment YAML, an explicit `YAMLCONF_CONFFILE`, and native `YAMLCONF_*`
environment definitions retain their provenance. No ancestor settings search is
performed. `django-environ` owns typed service URL parsing.

The compiler owns `COMPOSE_OUTPUT_DIR`, `COMPOSE_OUTPUT_MODULE`, the derived
`COMPOSE_ROOTS` and `COMPOSE_SNAPSHOT`, and the resulting ordered registry.
Structural inputs must be authored configuration; an environment-derived value
cannot silently change the installed app closure or generated module identity.
Runtime secrets and service URLs stay deployment inputs.

## App Graph And Settings

`INSTALLED_APPS` declares project roots, including ordinary third-party Django
apps. Each addon distribution advertises one entry point in `angee.addons`.
The compiler's native component index supplies distribution providers and requirements;
Django owns config selection, labels and lifecycle. Requirement markers are
interpreted for the base installation (`extra=""`); optional extras install
libraries without implicitly activating addon apps.

### Addon Discovery

An installed distribution is available; a root or required addon is enabled.
Those are different facts. The platform catalogue projects them from native
metadata and the loaded registry; it does not discover apps by scanning folders
or importing candidate models. A missing `apps.py` does not make a package a
library. A library is explicitly packaged without an addon entry point.

Installed and source catalogues read entry points and requirements from native
distribution metadata. Ordinary addons author `dynamic = ["entry-points"]`;
[hatch-angee](https://github.com/ang-ee/hatch-angee#readme) owns the naming rule.
Explicit entry points remain available for custom mappings and additional groups.

The catalogue reads installed distribution metadata and explicitly built wheel
artifacts. Authors and CI own builds and publication; catalogue refresh never
installs packages, executes build hooks, or materializes source. Native METADATA
and entry points own identity, including dynamically generated Hatch entry
points. Packaged `pyproject.toml` contributes capability facts only.

Optional addon Sources read local wheel directories or committed/CI-produced
wheel blobs through native VCS transport at one immutable revision. Preparation
finishes before the existing source CAS/reconcile transaction. Failed or changed
inputs preserve previous rows and sync time. Standard public/private index
installation remains with uv/operator, after which Platform reads the installed
metadata; direct uninstalled index browsing is not currently a source type. See
[built-distribution catalogue sources](../src/angee/platform_integrate_vcs/README.md).

Addon requirements belong in `[project].dependencies`. Foreign Django app edges
belong in django-compose's `AppConfig.requires_apps` extension; do not repeat
addon requirements there.
Identity collisions, missing requirements and cycles fail at the compiler owner.
Platform uses that same owner for proposed app selections in an isolated process.
Loaded dependency edges come from `COMPOSE_CLOSURE`; model and field views read
Django's registry and `_meta`, with contribution origins from emitted compiler facts.
Catalogue display remains metadata-only. Selection checks do not prove model
composition or migration safety; those remain explicit composition/history checks.
`requires_apps`, `autoconfig`, `Composable` and `compose_urls` are django-compose
extension contracts; they are not built-in Django behavior. Django owns
`AppConfig`, app population and the final `INSTALLED_APPS` registry.

## Autoconfig

An enabled app may contribute `autoconfig.SETTINGS` and a derived
`settings(namespace)` mapping. Native django-yamlconf owns nested keys and merge
operators such as `:append`. Keep contributions typed, deterministic and free
of model imports. The compiler merges them in enabled dependency order.

An addon contributes its own implementation registry entries and hooks. It does
not replace project roots or generated-output identity. Each capability owns
its callable validation and invocation; [`angee.base.hooks`](../src/angee/base/hooks.py)
owns the shared dotted-callable mechanics. Bare source-model tests use ordinary
Django settings with explicit registry inputs; they do not run a private settings
composer.

## Runtime Build And Import

Every participating source remains a native abstract Django model. Foundation
models inherit `Composable` through `AngeeModel`; plain Django participants
inherit `django_compose.models.Composable` directly.

| Class keyword | Intent |
|---|---|
| `concrete=True` | Emit this abstract source as a concrete model |
| `extends="label.Model"` | Donate fields and behavior to the same emitted record |
| `inherits="label.Model"` | Emit a multi-table child of that concrete parent |
| `demands="label.Model"` | Require an ancestor through the participant's MRO |

`concrete`, `extends` and `inherits` are mutually exclusive on one declaration;
`inherits` implies a concrete output. `demands` combines with a declaration.
Keep `Meta.abstract = True`, native managers, field definitions and cooperative
`super()` behavior on source owners. Dynamic factories pass the same keywords
to `type()`. Model labels, not generated Python imports, connect addon sources.

Capability integrations implement native structural protocols in
`django_compose.declarations`: literal model declarations, migration policy,
and artifact rendering. Register dotted integration classes in
`COMPOSE_INTEGRATIONS`; declare each artifact subtree explicitly. The compiler
owns deterministic publication and receipt checking. Serving code consumes
published results and never imports an addon build adapter.

### Addon-owned runtime migrations

An addon may supply a self-contained Django transition at
`compose_migrations/<app_label>/<name>.py`, outside conventional `migrations/`.
Its native `Migration` and pure `applies(ProjectState)` guard are compiler
inputs. The source label, name and exact body are durable identities; ordering
comes from Django dependencies and `run_before`, not filename order.

After compose, native `makemigrations` binds applicable transitions and authors
remaining changes. Review and commit the resulting native history. Copied bodies,
origin fingerprints and released names remain immutable. A false guard cannot
prove a data-only backfill ran. Partially upgraded deployments retain the original
chain. For a newly authored final-schema baseline, an addon owner may explicitly
declare that a particular transition is already satisfied:

```toml
[tool.angee.migrations.initial_baseline]
stage_fractional_position = "satisfied"
```

This is a promise about the baseline, including databases adopted later with
Django's `--fake-initial`; table existence alone does not prove it. Base's eager
settings callback annotates `django_compose.snapshot.discover_transitions` records
from the resolved closure. The compiler seals an inapplicable opted-in transition
as a native no-op history node, preserving its origin and source hash. Applicable
bootstrap/backfill work still runs; undeclared or ambiguous skips still fail.
Unknown transition names fail configuration. An explicit project
`COMPOSE_TRANSITION_SOURCE` remains authoritative. Do not edit a released source
migration to express this promise; retire its declaration when the transition is
retired under the native history policy.

For a retained initial migration that already satisfies the owner declaration,
use native `adopt_transition` explicitly. This attests that the named baseline
is covered by that declaration; the compiler does not infer historical data
completion from a false guard. Dry-run first, then review and commit the appended
empty origin alongside ordinary schema migrations. Existing files remain intact,
and native `migrate` applies and records the new node.

`MIGRATION_MODULES` is Django's routing contract. The compiler preserves tracked
histories, including history-only apps whose source addon has been removed.
Read-only checks never author missing packages. A disabled migration mapping
cannot receive an authored transition or a history reset.

The native drop guard refuses autodetected removal of populated storage.
Declare a reviewed data cutover when retiring data. Resources' retirement policy
may exempt only rows proved seeded by the retiring owner through its ledger;
other rows remain protected. Database aliases and routing remain Django-owned.
`check_migrations`, `retire_history` and `reset_migration_history` are explicit
native commands; retirement/reset require their owner-defined applied-history
and dependency proofs. They are never routine startup repair. Preserve any
historical imports still required by live migrations through narrow aliases.
See [migration policy](backend/guidelines.md#migration-history) and the
[migration procedure](../.agents/skills/angee-runtime/references/migrations.md).

### Composed addon dependencies

The root `angee` distribution requires the framework addon cohort. Each addon
owns its native requirements; consumer collections require their own members.
The root release tool changes the framework cohort and affected requirement
ranges. Examples, fixtures and the layout backend retain independent versions.
Native uv sources select local members or sibling packages during development;
no dependency compiler or generated requirements group participates.

### Runtime cleanup

The compiler owns its generated tree and receipt. Use its explicit composition
and history commands; do not delete output with an unguarded recursive command,
rewrite copied migration bodies, or treat an ignored directory as a history
retention policy. A replaceable model file and a tracked migration file have
different lifetimes even when they share an output package.

## Addon Declarations

An addon ships its own `pyproject.toml` as package data. Common metadata stays in
`[project]`; capabilities interpret only their own table:

- `[tool.angee.platform]` owns catalogue category.
- `[tool.angee.resources]` owns resource tiers and retirement declarations.
- `[tool.angee.graphql]` owns schema/domain declaration overrides.
- `AppConfig.rebac_schema` owns the permission schema path.
- Conventional Python modules own executable contributions.
- `web/package.json` owns web identity and external `angee.codegen` entries.

Resources are resolved relative to the addon's `resources/` directory. The
resource owner preserves canonical ledger paths and adoption keys. Removing a
resource declaration does not retire its rows. The web projector emits transport
facts; the schema-independent `@angee/app` codegen CLI consumes emitted SDL and
the web manifest to generate TypeScript. Python does not render schema-shaped TS.

## Serving

`angee.server` is an optional addon activated by serving capabilities. Enabling
web, GraphQL or MCP declares it through native requirements. Model-only projects
need no HTTP server. Ordinary Django URL settings remain valid.

Native `django_compose.urls` reads explicit `AppConfig.compose_urls`, with optional
prefix and namespace. Native `django_compose.asgi` reads `compose_asgi()` contributions
after Django setup, rejects collisions and owns async lifespan teardown.
GraphQL contributes its Channels WebSocket protocol; MCP contributes its HTTP
mount and fresh native lifespan. `angee.server` supplies HTTP defaults and
connection policy around those generic seams. Importing an app or its settings
starts no server or background task.

## Example Project Shape

```python
# settings.py — authored, imported in build and serving processes
from angee.base.conf import configure

configure(globals())
```

```yaml
# settings.yaml
INSTALLED_APPS:
  - example.notes
COMPOSE_OUTPUT_DIR: "{BASE_DIR}/runtime"
COMPOSE_OUTPUT_MODULE: runtime
ANGEE_DATA_DIR: "{BASE_DIR}/.angee/data"
```

`manage.py` selects `DJANGO_SETTINGS_MODULE=settings` and delegates to
`django_compose.management.execute`. The authored root and generated package
must be importable through normal Python installation/path semantics.

## Invariants

- Native packages install; explicit compose emits; provision mutates database state.
- One compiler owns closure, model composition, artifacts and migration binding.
- Base owns Angee defaults; server owns HTTP policy; each capability owns its facts.
- Serving verifies and imports output; it never authors history or repairs artifacts.
- Native history and required historical Python imports survive source retirement.
