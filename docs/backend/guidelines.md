# Backend Guidelines

Backend code is Python, Django, and the composer. It owns data, permissions,
transport-neutral business behavior, and generated contracts.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
backend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for backend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. Python dependency setup belongs in `pyproject.toml` and
`uv.lock`.

## Django-Native Rule

Angee is not a second framework on top of Django. It is a build-time composer
for Django apps.

Before adding an Angee abstraction, ask: does Django already have an object,
method, or convention that owns this fact?

Use Django's native owners:

- App facts live on `AppConfig`.
- Model behavior lives on models, managers, and querysets.
- Value coercion lives on fields.
- Command dispatch lives in Django management commands and `argparse`.
- Table names, app labels, migrations, and model metadata follow Django
  defaults.

Angee code should own only the composition seam: discovering addons, ordering
them deterministically, emitting runtime apps, merging schemas, syncing
resources, and failing fast on collisions.

A wrapper must prove it adds a real new concept. If it only forwards,
normalizes, or renames a Django object, delete it.

Django-native also means app-native. Addons are reusable Django apps with
conventional files: `addon.toml` declares the addon contract (and its presence is
the marker that makes the app an addon), `models.py` owns data and row behavior,
`managers.py` owns reusable row-set APIs when they outgrow the model module,
`schema.py` owns Strawberry declarations, `permissions.zed` owns REBAC structure,
`mcp_tools.py` owns MCP tool registration, `forms.py` owns Django form
validation/presentation, `admin.py` owns Django admin presentation, and
`management/commands/` owns CLI parsing. `apps.py` is optional — an addon needs one
only to run a Python seam (`ready()` / `import_models()`). Do not add a parallel
registry, loader, or naming convention until the native Django surface is proven
insufficient.

Before adding backend structure, pass the Django architecture gate:

- Use Django's object first: model, field, manager, queryset, `AppConfig`,
  management command, URLconf, migration, setting, or admin/form hook.
- Keep Django apps reusable. An addon may depend on declared upstream addon
  contracts, but it should not know the host project, consumer addon, route
  layout, or generated runtime package by import.
- Keep policy on Django owners and details at the edge. GraphQL resolvers,
  commands, resources, webhooks, OAuth callbacks, and vendor clients translate
  inputs to model/manager/queryset/service calls; they do not re-decide model
  rules, permissions, implementation keys, or schema shape.
- If two addons need the same backend behavior, move it to the base addon or
  framework owner that both compose. Do not copy a resolver, resource loader,
  SDK wrapper, settings parser, or permission rule sideways.

## Package Layering

The framework core is three packages with a one-way dependency rule that a test
enforces:

- `angee.base` is the model foundation (models, fields, mixins, managers,
  querysets, and model emission declarations). It must not import `angee.compose`,
  `angee.graphql`, or addon packages.
- `angee.graphql` is the GraphQL runtime (schema assembly, Strawberry helpers,
  serving, subscriptions, and SDL commands). It may import `angee.base`, never
  `angee.compose`.
- `angee.compose` is the build-time composer. It may import `angee.base` and
  discover plain Django addon configs, but no serving module (`asgi`, `urls`,
  `views`, `consumers`, `signals`, `models`, `graphql`) may import
  `angee.compose`.

The same one-way rule extends downward to the base addons under `addons/angee/`
(`angee.resources`, `angee.iam`, `angee.integrate`, `angee.operator`,
`angee.storage`): an addon
may import `angee.base` and `angee.graphql`, but never `angee.compose`. The
resource subsystem (`angee.resources`) is itself a base addon, not part of the
core — it owns the resource ledger described below.

Rules that follow from the layering:

- **Addon discovery is a Django app-registry concern**, not a build-only
  concern: serving code such as schema building enumerates Django's installed
  app configs and reads only the declaration attributes it owns. Serving code
  never imports `angee.compose` just to list addons.
- **An Angee addon is a Django app marked by a co-located `addon.toml`.** The
  manifest's presence is the marker (`angee.addons.is_angee_addon`); there is no
  `AppConfig` flag and no Angee base config to subclass. An addon needs an `apps.py`
  only to run a Python seam (`ready()` / `import_models()`); otherwise Django's
  auto-created `AppConfig` is enough. The declarative contract — `depends_on` (the
  ordering contract) plus the contribution seams — lives in `addon.toml` and is read
  through `angee.addons.addon_contract`.
- **The contribution seams default to what the addon directory reveals; an explicit
  manifest entry only overrides that default.** `schema.py` (defining `schemas`) →
  the GraphQL bucket, `permissions.zed` → the REBAC contribution, `web/package.json`
  → the web package (its `name`), `mcp_tools.py` (defining `register`) → the MCP
  tools. So a conventional addon declares only `[addon]` identity + `depends_on` +
  metadata (and any ordered `[resources]` tiers); it spells a seam out in the
  manifest only to override the convention (a non-default web package, or
  `[web].codegen`). The dependency graph, resource tiers, and metadata are never
  inferred — order and intent are not path-derivable. Each lifecycle step then reads
  only the contract it owns: `graphql` reads `contract.schemas`, `resources` reads
  the `[resources]` tiers, the web projector reads `[web].package` / `[web].codegen`,
  the MCP server reads `[mcp].tools`, REBAC sync discovers an adjacent
  `permissions.zed` by convention, stable serving imports conventional `urls.py` /
  `asgi.py`, runtime emission reads model-level `runtime = True`, and settings
  composition reads the addon's optional `autoconfig.py`.
- **There is a single app set and a single boot.** `DJANGO_SETTINGS_MODULE`
  points at `angee.compose.settings`, which imports the project's settings
  contract (`settings.yaml` or `settings.py` beside `manage.py`). YAML projects
  declare `INSTALLED_APPS` and `ANGEE_RUNTIME_DIR`; Python projects may declare
  those same facts directly. `angee.compose.settings` loads the project contract
  and calls `Composer(globals()).compose_settings()`, which expands the addon
  dependency closure and sorts the resulting app set, then gives Django the
  resolved `AppConfig` instances in `INSTALLED_APPS`. Framework boot apps
  (`angee.compose`, `angee.base`, `angee.graphql`) arrive through that same graph
  rather than a parallel hardcoded list. In app-populate phase 2,
  `ComposeConfig.import_models()` checks the generated runtime and imports
  concrete model modules before normal app model imports continue. `angee build`
  and `angee clean` may emit stale runtime sources during that hook only so
  Django can finish loading the generated model registry; no build/run app-set
  split exists.
- **The resource ledger is owned by the resource addon.** The composer discovers
  `angee.resources.models.Resource` as a normal addon source model and emits it
  under the `resources` label. `angee.base` must not import `angee.resources`.
- **Refer to an emitted concrete model through the app registry**
  (`apps.get_model("resources", "Resource")`), never by importing the generated
  `runtime/` tree.

## Rules

- Domain behavior lives on models, managers, and querysets.
- Manager/QuerySet canon: chainable read scopes live on a `*QuerySet` exposed
  through `Manager.from_queryset(...)`. Factories and mutations stay on the
  manager that owns the write.
- Model methods own instance invariants, state transitions, validation, and
  side-effect boundaries tied to one row. Managers own factories, upserts,
  reconcile/load flows, and writes that begin from a model class. QuerySets own
  chainable read predicates and reusable scoping. If a resolver, view, or command
  repeats a filter predicate, promote it to a QuerySet; if it mutates row state,
  promote it to a model or manager method.
- Cross-addon and generated-model references go through Django's app registry
  (`apps.get_model`, `apps.get_app_config`, `apps.get_app_configs`) and `_meta`.
  Never import generated `runtime/` modules or rediscover model/app facts by
  string parsing.
- GraphQL resolvers stay thin. They resolve the runtime model, actor/context, and
  input object, then delegate to the model, manager/queryset, action, or
  aggregate builder that owns the rule. If a resolver branches on field names,
  status values, permissions, or implementation variants, the owner is missing a
  method.
- GraphQL schema files declare Strawberry types, inputs, filters, buckets, and
  field-level resolver glue. They bind to composed runtime models and compose
  library primitives (`strawberry-django`, `crud`, `changes`, aggregate builders)
  instead of reimplementing ORM, permission, or serialization behavior.
- Model-backed `hasura_resource(...)` surfaces expose sqid public identity. Use
  `AngeeDataModel`/`SqidMixin` for concrete rows. For third-party Django models
  that Angee exposes but does not own, pass an explicit sqid public identity
  decoder to the resource instead of creating source-addon migration state. A
  raw primary-key compatibility path must be explicit and source-test-only.
- Management commands stay thin. They parse CLI arguments, load settings/context,
  and call the owner. Command modules should not contain reusable business logic,
  import generated runtime models directly, or duplicate resource/composer/schema
  behavior.
- Vendor SDK clients are details. Keep SDK request/response quirks in the
  provider addon or backend class that owns that vendor, and map them into
  Angee-owned models/actions at the boundary. Do not let SDK field names become
  framework/domain names unless they are the domain vocabulary.
- Source model discovery should follow Django model inheritance and explicit
  model-owned declarations, not naming or field-shape heuristics.
- Put behavior on the object that owns the shape, the Django way: coerce values
  with `Field.to_python`/`get_prep_value` instead of branching on field type from
  outside; ask `model._meta` (`get_field`, `label_lower`) and
  `Field.value_from_object` rather than re-decoding model shape; surface query
  behavior through `Manager.from_queryset`; and give objects classmethod factories
  and `deconstruct`-style methods to construct and serialize themselves. This is
  the backend application of **Find the owner** in `AGENTS.md` and the
  Django-Native Rule above.
- Compose behavior onto the class that owns the data. Settings construction
  belongs on `Composer`; runtime model materialization belongs on `Runtime`.
  Keep a module-level function only for orchestration that genuinely has no
  owner, and prefer forming a cohesive class even then. A dataclass that only
  holds fields while a sibling module mutates and emits from it is a missing
  class. Organizing behavior into named files and classes is what keeps the
  framework consistent and normalized: a class is a fixed home that forces
  related behavior together and resists the drift that loose, scattered
  functions invite.
- Imports go at the top of the module. A function-local or deferred import is a
  smell that a module boundary is wrong — an import cycle, or a layer reaching
  across a seam — so fix the seam (move the shared fact to its owning module, or
  invert the dependency) instead of hiding the import inside a function. Two
  exceptions, all narrow: a dependency that is genuinely optional at runtime
  (isolate it behind its own module), Django's app-loading order — an
  `AppConfig` module is imported in app-populate phase 1, before the registry is
  ready, so it must defer importing model classes (and signal wiring that pulls
  them in) until a method runs after `ready()` — and the ASGI application
  factory's import of Django/Channels serving modules after pytest-django or
  `django.setup()` owns setup. Mark such a deferral with a comment naming the
  reason; everywhere else, hoist. Within Angee's own source (`angee/` and
  `addons/angee/`) these are the only function-local imports allowed — phase-1
  deferrals, ASGI setup-order deferrals, and `TYPE_CHECKING` blocks. Probe optional
  or generated modules with `importlib.util.find_spec`
  (verifying each parent first) rather than `try/except ImportError`, so an absent
  generated `runtime/` reads as "not built yet," not a swallowed error.
- A pure renderer that takes its owner and returns a value with no other state may
  stay a module-level function in the owner's module; make it a method only when
  it reads more than one field of the owner or shares state with sibling helpers.
- A package `__init__.py` whose sole job is re-exporting a stable public API is a
  compatibility surface; `__all__` is allowed there (the usual "avoid `__all__`"
  rule targets ordinary modules).
- When restructuring or lifting existing code, reconstruct each module from its
  contract, tests, and these guidelines — do not paste or mechanically port the
  old code, and do not keep the old modules importable inside the `angee` namespace.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the owning model class.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural and owned by `django-zed-rebac`. Addons declare
  `permissions.zed` beside the owning app. Permission sync is the library's
  own `manage.py rebac sync`. Use the library's
  field-backed relations (`// rebac:field=...`) when a relationship is already
  represented by a Django FK or one-to-one field. See the REBAC section below for
  this project's fail-closed posture and its traps.
- For a vendor-backed capability, keep catalogue models pure metadata, model the
  connection shape at the row that stores its fields, and put the provider
  adapter choice on that owning row as a `backend_class`-style `ImplClassField`
  only when the persisted shape is otherwise the same. Name things in the
  domain's own terms, and keep side-effecting work on the operator — Django
  stays the catalogue.
- **Choosing how a row selects per-variant behaviour.** Classify by what varies:
  - *The row is one mutually exclusive concrete kind of a parent concept* →
    **Django child model**. The parent owns common identity, permissions,
    lifecycle, listing, and cross-kind actions; each concrete child owns its
    fields, tabs, actions, and row behavior. Use this when a parent plus required
    one-to-one "related model" would otherwise be manual polymorphism.
  - *A downstream addon adds optional capability fields to the same kind of row*
    → **model `extends`**. The base row remains the same domain object; extension
    fields are additive and may be blank/off. OIDC login fields on
    `integrate.OAuthClient` are the canonical shape.
  - *Only behaviour differs, open set (addons contribute impls) while persisted
    fields stay the same* → **one concrete model +
    `angee.base.fields.ImplClassField`** naming a non-model
    strategy/client/backend class. Name the field by the role it plays
    (`backend_class`, `provider_type`), not by a generic "implementation" label.
    One table (unified list/reconcile, no field duplication); the impl is an
    **explicit per-row** choice, **never** derived from a vendor slug (a vendor
    can have several impls/accounts).
  - *Only behaviour differs, closed framework-known set* → a `StateField` + an
    eager **handler registry** (`integrate.credentials.register_handler`/`handler_for`).
    The row stores the enum value; the kind projects as a GraphQL enum.
- **Enum-backed fields use `StateField`, never `CharField(choices=…)`.**
  `StateField` wraps django-choices-field's `TextChoicesField`, so strawberry-django
  renders a native GraphQL enum straight from the `choices_enum`. A plain
  `CharField` with `choices` renders as a bare `String` and silently drops the enum
  at the API boundary — never use it for an enumerated value. `StateField` is for an
  actual closed enum the row *carries* (status, platform, source, kind-of-credential).
  A **type discriminator that selects mutually-exclusive concrete kinds is not an
  enum field at all** — it is a Django child model (the first branch above): the
  concrete child *is* the kind, so a `Party`→`Person`/`Organization` split has no
  `kind` column. Reach for a child model, not a `StateField`, when the kinds carry
  their own fields (e.g. a `Person` linking to an `iam.User` that an `Organization`
  never has).
- **Integration implementations are concrete integration children.** The
  top-level `integrate.Integration` row is the shared connection identity and
  lifecycle. Concrete integration kinds such as inference providers and VCS
  bridges are child models; their forms open from the integration surface and
  contribute implementation-specific tabs/related tables. A child model may carry
  its own `backend_class` when several SDK/protocol adapters share that child's
  persisted shape. Do not store a second generic `impl_class` on the child: the
  child model is the integration implementation; the backend field is the adapter.
- **A row-selected impl is stored as a registry key, never a dotted path.**
  `ImplClassField(base_class=…, registry_setting=…)` stores a short key and
  resolves it against a Django setting mapping keys to dotted import paths; an
  addon contributes its impl into that setting through `autoconfig` (a yamlconf
  dotted key, `"ANGEE_…_CLASSES.<key>": "<dotted.path>"`). So a writable column
  never feeds `import_string` (the path comes from composed, trusted settings,
  like an addon's `schemas` reference), the available impls are a composition
  fact rather than a base-model import, a project can remap a key to its own
  class, and `manage.py check` validates every configured path imports and
  subclasses `base_class`. Because every addon has contributed by schema-build
  time the key set is closed, so the field is a `TextChoicesField` and
  `strawberry-django` renders the GraphQL enum natively (like `StateField`). It
  therefore requires a **non-empty** registry: an addon whose impl set could
  otherwise be empty registers a noop/null-object default (storage's `local`;
  integrate's `none` VCS client), so a composition always has one selectable
  impl and the enum is never empty.
- Cross-addon dependencies are one-way (e.g. `integrate → iam`, never the
  reverse); reject a bridge/diamond addon that would couple both ways.
- GraphQL authoring is native Strawberry. Addons expose a `schemas` mapping in
  conventional `schema.py` modules. Each named schema contributes into fixed
  buckets (`query`, `mutation`, `subscription`, `types`, `extensions`,
  `type_extensions`, `input_extensions`); Angee merges buckets across addons and
  builds one Strawberry `Schema` per name.
- **GraphQL types and enums bind to the composed *runtime* model, never the
  abstract source class.** Resolve the model with `apps.get_model("app", "Model")`
  (the concrete emitted class), not `from app.models import Model` (the abstract
  source); the runtime class is the post-composition source of truth for fields,
  relations, and choices. A registry-backed enum (`ImplClassField`) is read off the
  runtime field, so the GraphQL enum already reflects every addon's contributions.
- **Extension is symmetric across five axes — extend, never edit the owner — and
  the schema is built after the runtime is composed, so all five apply
  post-composition with the dependency staying one-way (downstream reaches up; the
  upstream never references down).** Add a *concrete subtype* of a parent row with
  a Django child model when exactly one concrete kind applies; add a *field* to
  another addon's model with an `extends = "app.Model"` source model when the same
  row gains optional capability fields; add a *value* to an open enum with an
  `ImplClassField` registry (settings-keyed, one impl class per key — use it only
  when each key has genuinely distinct implementation code, not as a workaround for
  a closed `TextChoices`); add a *field onto another addon's GraphQL type* with
  native `strawberry_django.type(RuntimeModel, name="UpstreamType", extend=True)`,
  listed in the `type_extensions` bucket — Strawberry owns the extension merge and
  strawberry-django resolves any relation projection from its model registry (e.g.
  `iam_integrate_oidc` adds fields to `OAuthClientType` without `integrate`
  importing it); add *fields onto another addon's handwritten GraphQL input* with
  native `strawberry.input(name="UpstreamInput", extend=True)` listed in
  `input_extensions`. Input extensions are the write-side equivalent: they name the
  target input and add fields only; Strawberry merges multiple donors additively in
  addon order and fails fast on field-name collisions. Type and input extensions
  are global-additive, like a model `extends`: the field lands on the target
  wherever it appears (the bucket only gates registration), so reference a field
  type that some bucket lacks and that bucket's build fails loudly rather than
  leaking.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## REBAC

REBAC is owned by `django-zed-rebac` (see the Rules entry and `docs/stack.md`).
This project runs **fail-closed**: `REBAC_STRICT_MODE=True` and
`REBAC_SUPERUSER_BYPASS=False`, so every actor — superusers included — reaches
data through REBAC, never a queryset bypass.

- Bracket every server-side read/write in `system_context`/`asystem_context` and
  resolve the actor with `@rebac_subject`; a bare `Model.objects.create()` under
  an actor is denied.
- A per-row `create` permission cannot gate an insert (the unsaved row has no id →
  deny). Gate explicitly with a preflight (`has_access("write")` /
  `rebac.check_new`), then insert via `row.sudo()` + `save()`; `.sudo()` never
  auto-clears, so follow with `.with_actor(actor)`.
- Model universal-admin reach as a const-backed relation
  (`relation admin: angee/role // rebac:const=admin`, no tuple or FK) resolving
  membership in `angee/role:admin`. Admin-gate a table-less/synthetic resource
  with a `managed=False` abstract anchor model (passes `rebac.E009`, emits no
  table) plus that const admin, and keep an `| angee/role:admin#member` arm in
  `member` or `rebac.W004` fires.
- There is no `rebac_roles` command — grant roles with `rebac.roles.grant`. A
  superuser created without a real `save()` (bulk_create, loaddata, or skipped as
  unchanged) is never in `angee/role:admin#member`, so const-admin reach fails
  until re-granted.
- Never `select_related` a REBAC-guarded relation into an actor-scoped queryset —
  it fails live ("loaded N rows outside actor scope") while passing unit tests.
  Resolve the field elevated by FK id under `system_context`, and verify by
  rendering the live page, not just the test.
- The relationship store has two storage modes: composed projects run the
  FK-backed `registry` mode (`angee.base` autoconfig) while bare
  `tests/settings.py` runs the library's `denormalized` default — a
  registry-only break therefore passes unit tests and fails live. Since
  django-zed-rebac 0.14 the registry queryset storage-translates the whole read
  API — `filter`/`exclude`/`get` kwargs, `Q` objects, and
  `values`/`values_list`/`order_by`/`annotate` field names — so query with the
  natural denormalized names; instance attributes (`row.subject_id`) are
  portable too (the registry manager eager-joins them). Any read shape beyond
  that API must be verified in both modes — regression-test permission-hub
  surfaces under `override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")`.
- Derive operator/edge token scope from `<ns>/role:<id>#effective_member` (folds
  in role-hierarchy `includes`), never `roles_of`/`roleRefs` (a direct-grants UX
  hint that under-grants).
- `rebac sync` persists the zed into DB `Schema*` tables, and the system checks
  gate every subcommand on that persisted state — so editing the zed can deadlock
  the sync. Unstick with `rebac --skip-checks sync --force-overwrite --yes` then
  `rebac sync`; never smoke-test a zed against the shared example DB.
- If a removed or renamed definition in an otherwise composed package fails
  `rebac.E009`, run the check-free `reconcile_permissions` first; it prunes stale
  package-managed schema rows before `makemigrations` / `rebac sync` can run.
- When an addon removes its last REBAC resource, keep an empty package-owned
  `permissions.zed` with a bumped schema revision until old package-managed rows
  have been pruned; deleting the file makes `rebac sync` skip the package and
  strand stale definitions in existing databases.

## Pitfalls

Hard-won traps — the wise learn from others' mistakes (`docs/guidelines.md`).

- **`crud()` create `full_clean`s the input, so model + input defaults must agree.**
  `strawberry-django`'s create builds a dummy instance from the input and calls
  `full_clean()` before saving — two traps follow. (1) A `JSONField(default=dict)`
  (or `default=list`) needs `blank=True`: Django counts `{}`/`[]` as blank, so a
  `blank=False` container default fails `full_clean` ("cannot be blank") on every
  create. (2) An optional create-input field over a **non-null** column must
  default to `strawberry.UNSET`, never `None` — `None` is submitted as an explicit
  null that overwrites the model default (e.g. `status`/`config`), and
  `full_clean` then rejects the null. Mirror this for any new `crud()` input.
- **`uv run` tool shebangs are stale** — run Python tools by module:
  `uv run python -m pytest`, `uv run python -m mypy angee addons`,
  `uv run python -m ruff check .`. Bare `uv run pytest`/`mypy` fail to spawn.
- **Regenerate the SDL after `angee build`** — re-run `manage.py schema`
  (+ `--check`). A missing `runtime/schemas/*.graphql` makes Vite ENOENT and the
  SPA silently fails to mount (every e2e fails at list load) while `:5173` still
  returns 200; check `runtime/schemas/` before chasing app/test regressions. (The
  dev server regenerates it for you — see the `runserver` pitfall — but a manual
  `angee build` outside `angee dev` still needs the explicit `schema` step.)
- **Agent runtime auth is a `(runtime × provider × credential-kind)` fact, not provider-only.**
  The `AgentRuntime` an agent's `runtime_class` selects (`angee.agents.runtimes`) owns how a
  credential becomes container env *and* the synced secret payload (`auth_env` /
  `auth_secret_value`) — the same Anthropic OAuth token feeds Claude Code's
  `CLAUDE_CODE_OAUTH_TOKEN` but OpenCode reads only `ANTHROPIC_API_KEY`. The inference
  backend stays the owner of vendor-native primitives (`api_key_env`, the credential value).
  A runtime that cannot consume a credential kind refuses it in the readiness gate, never
  rendering a service that silently degrades to a fallback model. **OpenCode + Personal-Plans
  OAuth is off by default** (`ANGEE_OPENCODE_OAUTH_ENABLED`): it needs a community auth plugin
  baked into the opencode image (the `OPENCODE_ANTHROPIC_AUTH_PLUGIN` build arg) and using a
  Pro/Max token there violates Anthropic's ToS — enabling it without the plugin silently drops
  Anthropic from OpenCode's model list.
- **`angee dev` serves via Angee's `runserver` override, not `uvicorn --reload`.**
  `angee.compose` ships a `runserver` that runs `ASGI_APPLICATION` under uvicorn
  supervised by Django's follow-imports autoreloader (mirrors Daphne's override).
  It needs no `--reload-dir`: Django watches imported source — consumer/base addons,
  framework core, *and* editable deps — and never the generated `runtime/` (each
  child re-emits before its reloader snapshots), so a model edit reloads once. Don't
  reintroduce `uvicorn --reload`/`--reload-dir` heuristics in the stack template. The
  boot regenerates the SDL when `ANGEE_DEV_SDL=1` (set only by that command), so a
  live edit refreshes `runtime/schemas/*.graphql` and Vite HMRs; `schema --check`
  stays a real drift gate because management commands never import `angee.asgi`.
  Generated files (runtime models + SDL) are written atomically via
  `angee.fs.write_atomic`. The override also hard-exits the autoreloader child on
  reload: open uvicorn/channels WebSocket work can leave non-daemon runtime threads
  alive, so Django's default `sys.exit(3)` can wedge the child on a dead listener.
  Install `pywatchman` for event-based (vs 1s-poll) reload.
- **Each running stack needs a unique compose project name *and* edge port.** The
  stack `name:` becomes the docker-compose project name, and the agent chat
  WebSocket the browser opens rides the stack's `ingress.port` (the leased
  `edge_port`). Two stacks sharing a `name:` — e.g. every dev workspace defaulting
  to `notes-angee` — make Compose merge their containers into one project: one
  stack's agent ends up fronted by another stack's edge (or none), and the chat
  socket 1006s ("no response from the edge"). The dev workspace template scopes
  both per workspace (`project_name: "${inputs.example}-${workspace.name}"` and a
  leased `operator.port_pool.edge`); keep `name:`/`edge_port` workspace-unique when
  adding a stack or service template.
- **`makemigrations` must name every changed app** — include `resources` (and
  `base`) or `resources load` fails with `no such table: resources_resource`.
- **A resource yaml loads only when listed** in the addon's `addon.toml`
  `[resources]` manifest (`{tier = [paths]}`); an unlisted file silently
  loads nothing.
- **Give a model an opaque public id by mixing in `SqidMixin` and declaring
  `sqid_prefix = "abc_"`** — the one fact that varies per model. The shared
  `angee.base.fields.SqidField` reads that prefix in `contribute_to_class`; don't
  re-declare the column. The field is NULL-safe by design, because a sqid can be
  selected through a nullable join where `django_sqids.SqidsField` crashes on a
  NULL (REBAC `// rebac:field=` arrows run over nullable FKs).
- **A status field is read/write-asymmetric** — GraphQL serializes it on read as
  the uppercase enum NAME (`ACTIVE`) but the writable `Patch.status` `String`
  takes the lowercase model value (`"disabled"`).
- **Intersect write-only fields out of the read/return selection** — a field
  absent from the SDL read type (e.g. `password`) makes the detail query invalid
  and the form loads blank if it is selected.
- **Validation surfaces two ways** — Django `ValidationError` flows through
  `extensions.validationErrors` (camelCased), but GraphQL input-coercion errors
  fire before resolvers and never reach it, so guard required inputs client-side
  from `rootFields.requiredCreateFields`.
- **In test-client logins pass the backend** —
  `force_login(user, backend="angee.iam.auth.ModelBackend")`; the default backend
  order is chosen for runtime authentication concerns and may not be the session
  reload backend a focused test wants.
- **Login throttling belongs at the IAM auth seam.** Do not add per-view or
  per-test throttles; IAM composes `django-axes` at the `authenticate(request=...)`
  backend/signal path, so the password GraphQL mutation stays a thin caller.
- **Row locks must keep the SQLite floor.** Wrap `select_for_update()` through the
  owning queryset/manager's feature-gated helper; SQLite is a supported backend
  and raises `NotSupportedError` if a caller takes a row lock unconditionally.
- **A gated factory that uses `sudo()` must restore the actor before returning.**
  Elevated writes may be necessary to create the row, but callers continue under
  the original actor. Capture `current_actor()` before the elevated block and
  rebind the returned instance with `.with_actor(actor)` after save.
- **Publishers wire during schema build, not schema import.** GraphQL schema
  modules declare subscription surfaces; `GraphQLSchemas` connects publishers
  from declared `changes` metadata when a schema is built, so importing a schema
  for SDL/tests never mutates process-global signal state. Processes that never
  build a schema do not publish changes; base and consumer addons behave
  identically because no addon owns its own publisher label list.
- **`AngeeModel` managers/querysets must keep the canon.** If a model customizes
  `objects`, its queryset class must derive from `AngeeQuerySet`; otherwise
  shared methods such as public-id lookup, actor scoping, and elevated reads drift
  between models.
- **`MIGRATION_MODULES` may be assigned during app populate only for generated
  runtime apps.** That exception belongs to composed settings/runtime boot; do
  not use it as an addon-local shortcut or a way to hide source-model migration
  state.
- **`EncryptedField` keys are bound to `model._meta.label_lower` plus field
  name.** Renaming a model/app/field changes the derived key. Plan
  `ANGEE_FERNET_KEYS`/`MultiFernet` rotation before such a rename, and treat one
  corrupt row as a row-local unreadable value, not as a reason to break list
  queries.
- **Data-resource field widgets are backend-owned vocabulary.** Add or rename
  widget keys in `angee.graphql.data.field_classification` with the matching
  frontend renderer; resource callers declare fields, not ad hoc widget strings.
- **After adding or moving an addon** run `pnpm install`, and delete any stale
  gitignored `runtime/*/migrations/*.py` that imports a moved module before
  `makemigrations`.
- **OAuth/OIDC outbound requests must send an honest, non-browser User-Agent.**
  Anthropic's token-endpoint edge 429s spoofed browser/curl User-Agents with a
  `rate_limit_error` (before any auth check) and 403s urllib's `Python-urllib`
  default; an honest client UA passes. `angee.integrate.oauth.client` owns the value
  (`USER_AGENT`); never reintroduce a browser spoof or fall back to urllib's
  default.
- **Anthropic's JSON OAuth token exchange must echo redirect `state`.** Standard
  OAuth validates state before the token POST and does not send it, but
  Anthropic's public-client JSON token endpoint rejects that request as malformed
  without the state field. Keep the exception inside
  `angee.integrate.oauth.client`'s JSON shim; do not move it to the frontend,
  generated callback route, or generic form-token path.
- **TLS trust is an environment concern, not a per-call one.** Which CA roots we
  trust is owned by the runtime, set once — never threaded as an `ssl_context`
  through each outbound HTTPS call. Outbound code uses the stdlib default context
  (`ssl.create_default_context()`), which OpenSSL resolves against the system
  trust store and honours `SSL_CERT_FILE`/`SSL_CERT_DIR`. A dev mac trusts via
  Homebrew `ca-certificates`; an environment that lacks a CA store (a minimal
  container, a bare CI/agent sandbox) is fixed *there* — install OS
  `ca-certificates`, or `export SSL_CERT_FILE="$(python -m certifi)"` at bootstrap
  — not by adding `certifi` plumbing to call sites. Backend outbound HTTP has one
  owner already: `angee.integrate.http.HttpClient` (`self.http`), which builds the
  one context; route new outbound calls through it rather than hand-rolling
  `urlopen` + context.
- **An `ImplClassField` builds its enum at model-import time from its
  `registry_setting`** — the key→path mapping (e.g. `ANGEE_STORAGE_BACKEND_CLASSES`)
  is supplied by the owning addon's `autoconfig`, so every settings module that
  installs the addon must carry a **non-empty** mapping, including a bare module
  that skips the composer (`tests/settings.py` declares storage, integration,
  VCS, inference, and OAuth provider registries explicitly). An empty
  registry raises `ImproperlyConfigured` at import — give the addon a
  noop/null-object default so the set is never empty. The column stores the key
  (`local`), never a dotted path.
- **Never name an addon module after a third-party top-level package it imports.**
  `unittest` discovery inserts the discovery-root directory onto `sys.path`, so an
  addon's `mcp.py` that does `from mcp.server… import …` becomes an importable
  top-level `mcp` that shadows the real package — `ModuleNotFoundError: 'mcp' is not
  a package` during a test run, while a single-module run and `manage.py check` pass.
  Name such a module for its role, not the library (the MCP tool seam infers — or
  resolves a `[mcp].tools` override to — `mcp_tools.py`, not `mcp.py`).

## Framework Contracts

Framework contracts should be self-explaining in code. Add docstrings to public
modules, classes, methods, functions, declarative manifest attributes, and public
module-level constants. Add docstrings to private helpers when their role is not
obvious from the function name and signature. Do not maintain a parallel spec, field inventory, or model
API list for behavior that can live clearly beside the code.

The addon's `addon.toml` is the declarative manifest (its contract owner is
`angee.addons.AddonContract`); when an addon carries a Python seam, its `AppConfig`
owns addon-local *interpretation*. Use Django's own facts before adding an Angee
fact: the addon root is `AppConfig.path`, source models live in `models.py`, and
GraphQL contributions live in `schema.py`. Put validation, normalization, and path
resolution for one addon on the object that owns the data — its `AppConfig` (the
`ready()` / `import_models()` seam is the reason an addon adds an `apps.py`), a
model/manager, or a runtime build object for composition — not on loose functions;
keep a function loose only for orchestration no single object owns. Put the manifest
keys and their exact authoring forms in the `AddonContract` docstring, not in this
guideline.

Before decomposing backend code, classify each fact by its Django owner:

- Persisted choices live beside the model field, usually as model-owned
  `TextChoices`.
- Row-set behavior lives on managers and querysets.
- Instance behavior lives on model methods and properties.
- Addon declaration and path-resolution behavior lives on `AppConfig`.
- Management commands parse arguments and dispatch to the owning model, manager,
  service, or composer function.
- Compatibility facades exist only for an explicit compatibility promise.

The project settings contract declares project facts; Angee owns Django
composition wiring. By default, keep `settings.yaml` beside `manage.py` and set
only the deliberate composition facts there, especially `INSTALLED_APPS` and
`ANGEE_ADDON_DIRS` / `ANGEE_RUNTIME_DIR`. `ANGEE_PROJECT_SETTINGS` may point at a
project Python settings module when the project needs one. `angee.compose.settings`
loads Python settings first, overlays `settings.yaml` with django-yamlconf,
evaluates `angee.compose.defaults` as the base Django settings module, and asks
`Composer(globals()).compose_settings()` to compose `INSTALLED_APPS`,
`MIGRATION_MODULES`, import paths, and addon autoconfig.
Addon autoconfig uses yamlconf-style `SETTINGS` keys: plain keys are defaults,
`:append` / `:prepend` keys always merge, dotted keys update nested dictionaries,
`:raw` protects literal braces, and declared `ANGEE_*` addon settings may be
overlaid by same-named process environment values from the stack. Use
`settings.py` only when the project truly needs Python-computed settings. Angee
treats yamlconf errors as Django configuration failures and rejects implicit
ancestor `settings.yaml` files; only the project file and an explicit
`YAMLCONF_CONFFILE` may contribute file-backed settings. Generic typed yamlconf
environment overrides still require `:jsonenv`.
Anchor project defaults to `BASE_DIR`, never to the current working directory.

Keep `angee` as a namespace package. Do not add an `__init__.py` at either
namespace root (`angee/` for the framework, `addons/angee/` for the base
addons); split addon distributions must be able to contribute packages under the
shared `angee.*` namespace.

Avoid `__all__` unless a module has a concrete star-import or compatibility
requirement. Public API should usually be obvious from module names, object
names, and docstrings.

## Naming

Naming is structural: Django and the composer both locate code by name, so a
wrong name is a broken contract, not a style nit. Django is the reference — match
it exactly.

- **Modules** are lowercase, single-word, named by role: `models.py`,
  `managers.py`, `admin.py`, `forms.py`, `urls.py`, `apps.py`, `signals.py`,
  `mixins.py`, `validators.py`, `fields.py`, `backends.py`.
- **Structural directories** are fixed and discovered by name — never rename them:
  `migrations/`, `management/commands/`, `templatetags/`, `templates/`,
  `backends/`.
- **Packages / addons** are short and lowercase — no CamelCase, no stray
  underscores (`auth`, `contenttypes`, `storage`) — and match the addon label.
- **Classes** are PascalCase with a role suffix that mirrors the module: `*Field`,
  `*Mixin`, `*Manager`, `*QuerySet`, `*Form`, `*Admin`, and `*Config` for the
  `AppConfig`.
- **Methods / functions** are snake_case and verb-first from a stable vocabulary:
  `get_*` (accessors), `is_*` / `has_*` (booleans), `as_*` / `to_*` / `from_*`
  (conversions), `create_*` / `save_*` / `delete_*` (mutations);
  `_leading_underscore` for internal. Settings and constants are `UPPER_SNAKE`.
- **camelCase only when extending an external API that uses it** (e.g. Django's
  `unittest` assertions). Otherwise never.

## Checks

Run the narrowest relevant check while editing, then the broad check before
handoff:

Before adding a backend abstraction, search for the native owner first:
`rg "AppConfig|schemas|permissions|resources|autoconfig"`,
`rg "QuerySet|Manager.from_queryset"`, and
`rg "apps.get_model|get_app_configs"`. If the change introduces or extends a
seam, add a focused guard in the owning test area: layering in
`tests/test_layering.py`, addon/AppConfig contracts in app tests,
settings/autoconfig/app graph behavior in `tests/test_settings.py`, runtime
emission in `tests/test_compose.py`, and schema composition in GraphQL tests.

```sh
uv run python -m ruff check . --no-cache
uv run python -m mypy angee addons
uv run python -m vulture
uv run python -m pytest
uv run examples/notes-angee/manage.py angee build --check
```

Use the `python -m` module form (see Pitfalls: bare `uv run pytest`/`mypy` fail to
spawn on this repo's venv). If a command is not wired yet, say so plainly.
