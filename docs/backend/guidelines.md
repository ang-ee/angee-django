# Backend Guidelines

Backend code is Python, Django, and the composer. It owns data, permissions,
transport-neutral business behavior, and generated contracts.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
backend-specific layer applied during the Build step.

Use [the composer owner map](../composer.md#owner-map) for composition APIs,
[the glossary](../glossary.md) for terms, and [checks](../checks.md) for commands
and their execution roots. This page owns backend design rules and recurring
failure modes; implementation details stay beside their code.

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

- Django app identity and lifecycle live on `AppConfig`; addon declarations live
  in `addon.toml` and are validated against that identity.
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
`management/commands/` owns CLI parsing. `apps.py` is optional; use it to customize
native Django config or lifecycle hooks such as `ready()` / `import_models()`.
An addon without that customization uses Django's auto-created config. Keep
declaration facts in the manifest even when an explicit config exists. A proven
gap requires extending the native owner or adding the smallest canonical
composition seam at the owning level. It never authorizes a parallel registry,
loader, or naming convention alongside the existing owner.

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

The framework wheel and the GraphQL folder addon have a one-way dependency rule
that layering tests enforce:

- Core modules under `angee/` never import folder addons, including test support.
  Reusable addon test compositions live in the owning addon.
- `angee.base` is the model foundation (models, fields, mixins, managers,
  querysets, and native tracking mixins). It must not import `angee.compose`,
  `angee.graphql`, or addon packages.
- `angee.graphql` is the `addons/angee/graphql` folder addon's GraphQL runtime (schema
  assembly, Strawberry helpers, serving, subscriptions, and SDL commands). It
  may import `angee.base`, never `angee.compose`.
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

### Composition owners

Use these owners instead of maintaining another contract in an addon:

| Concern | Owner and durable rule |
|---|---|
| Addon discovery and declarations | [`angee.addons`](../../angee/addons.py) binds hatch-angee's native manifest to Django's config. `addon.toml` is the marker and declaration source; do not copy its configurable values onto `AppConfig`. |
| Settings and app ordering | [Settings bootstrap and app graph](../composer.md#settings-bootstrap) compose one app set. Keep source-model imports out of settings loading; do not create separate build/run registries. |
| Capability conventions | [Addon declarations](../composer.md#addon-declarations) identifies the readers. Each capability reads its own manifest section and conventional defaults; dependencies, resource tiers, and intent stay explicit. |
| Models and runtime import | [Runtime build and import](../composer.md#runtime-build-and-import) describes source selection and Django phase 2. Use narrow abstract contributors; let Django own inheritance, field cloning, managers, and registration. |
| Migration history and cleanup | [Addon-owned runtime migrations](../composer.md#addon-owned-runtime-migrations) and [migration pitfalls](#migrations-and-runtime) distinguish generated sources from durable history. |
| Resource load hooks | [`ResourceLoadMixin`](../../addons/angee/resources/mixins.py) is the resources-owned terminal mixin. Contributors delegate exactly once through `super()`, even when skipping local work; an exception aborts the transaction, so never delegate in `finally`. |

- **Compose addon discovery through its native owners.** Follow the
  [discovery flow](../composer.md#addon-discovery) for available candidates,
  Django identity, and persisted catalogue state. Serving code never imports
  `angee.compose` just to list addons.
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
- Multi-owner setup belongs to one transactional verb. Projects'
  [`setup_from_task`](../../addons/angee/projects/models.py) calls cooperative
  `apply_setup` hooks; each contributor consumes native values from its typed
  `input_extensions` contribution and delegates
  once. Replays recheck authority and validate the original setup-receipt fingerprint;
  they do not reset later edits. Resume existing partial rows through their
  owners instead of issuing independent browser writes.
- Actor summary fields compose native SQL scopes before counting. Proposals'
  [`clarification_waiting_users`](../../addons/angee/proposals/models.py) owns
  both recipient projections and attention counts, so hidden questions and
  answered recipients cannot inflate dashboard totals.
- A container scope key is an explicit filter-only contract, not a readable
  relation. [`hasura_container_scope_fields`](../../addons/angee/work/models.py)
  lets a resource declare stable related keys such as `queue__slug`; the
  [Hasura owner](../../addons/angee/graphql/data/hasura.py) keeps the root actor
  scope while permitting this membership test without container read access.
  Container-scope declarations also admit their keys as filters; do not declare
  them twice. Ordinary relation filters and projections retain their redaction guards.
- **Do not add a write API without a consumer.** Delete uncalled write commands;
  keep required writes on their owning manager/queryset.
- Model methods own instance invariants, state transitions, validation, and
  side-effect boundaries tied to one row. Managers own factories, upserts,
  reconcile/load flows, and writes that begin from a model class. QuerySets own
  chainable read predicates and reusable scoping. If a resolver, view, or command
  repeats a filter predicate, promote it to a QuerySet; if it mutates row state,
  promote it to a model or manager method.
- **Database routing:** Django routers own database selection, including the
  native fallback to an instance's `_state.db`. Do not thread database aliases
  through Angee methods or hooks. Use native relation access, `refresh_from_db`
  when deferred or stale fields need reloading, and ordinary `atomic` and
  `on_commit` boundaries while preserving locks and batching. A declared
  read-only external import source may select its database explicitly.
  Multi-database support extends Django with a router for static model placement;
  add operation scoping only for a concrete need. REBAC-managed models must write
  to the default database, together with the relationship and resource stores; the
  [base system check](../../angee/base/checks.py) validates configured routers
  against that requirement.
- External side effects and DB reflection are separate phases. File edits,
  daemon calls, network calls, and other non-DB effects never run inside
  `transaction.atomic`; the following DB mutation path names its transaction
  owner and `system_context` reason. Platform install, agent provisioning, OAuth
  flows, and resources loading all follow this two-phase shape.
- Cross-addon and generated-model references go through Django's app registry
  (`apps.get_model`, `apps.get_app_config`, `apps.get_app_configs`) and `_meta`.
  Never import generated `runtime/` modules or rediscover model/app facts by
  string parsing.
- A marked catalogue model belongs to exactly one resource tier for all seeders;
  the resources loader enforces each manifest tier against the model's declared
  `catalogue_tier`.
- GraphQL resolvers stay thin. They resolve the runtime model, actor/context, and
  input object, then delegate to the model, manager/queryset, action, or
  aggregate builder that owns the rule. If a resolver branches on field names,
  status values, permissions, or implementation variants, the owner is missing a
  method.
- GraphQL schema files declare Strawberry types, inputs, filters, buckets, and
  field-level resolver glue. They bind to composed runtime models and compose
  library primitives (`strawberry-django`, `hasura_model_resource`, `changes`,
  aggregate builders) instead of reimplementing ORM, permission, or serialization
  behavior.
- Declare computed GraphQL field dependencies with native Strawberry-Django
  `only`, `select_related`, `prefetch_related`, and `annotate` hints. Computed
  fields promote their model-owned aliases through native `annotate` hints only
  when selected; filter/order aliases stay unselected until the SQL uses them.
  See [the Hasura adapter](../../addons/angee/graphql/data/hasura.py) and
  [workflow decision fields](../../addons/angee/workflows/schema.py).
  Dependencies include inherited `AngeeNode.display_name`: bind its existing
  resolver on the concrete type with
  the fields its model's `__str__` actually reads. Test narrow selections at
  multiple row counts; selecting the underlying field elsewhere can hide a
  deferred-field N+1.
- Model-backed `hasura_model_resource(...)` surfaces expose sqid public identity. Use
  `AngeeDataModel`/`SqidMixin` for concrete rows. For third-party Django models
  that Angee exposes but does not own, pass an explicit sqid public identity
  decoder to the resource instead of creating source-addon migration state. A
  raw primary-key compatibility path must be explicit and source-test-only.
- Management commands stay thin. They parse CLI arguments, load settings/context,
  and call the owner. Command modules should not contain reusable business logic,
  import generated runtime models directly, or duplicate resource/composer/schema
  behavior.
- Declare resource query intent once on the resource; finalize its capabilities
  from the final composed schema and active execution backend. Relation identity
  comes from the target resource's identity policy, never from a display label or
  a consumer's guessed field name. Keep output values distinct from comparison
  input domains, and group summaries distinct from optional drill capability.
  Extend native lookup seams for database operators; never advertise an operator
  that the exposed comparison input or executor cannot accept.
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
  Move policy from helpers that interpret or mutate a passive data holder onto
  that owner. A module-level function is appropriate for orchestration when no
  participant owns the rule, or a pure transform with no natural owner. Choose
  the smallest native shape; creating a class does not itself establish ownership
  or remove duplication. Apply the shared [owner decision tree](../guidelines.md#put-behavior-on-the-owning-object).
- Imports normally go at module top. Fix accidental cycles and dependency
  violations at their owner rather than hiding them with deferred imports.
  Narrow deferrals may serve a named lifecycle boundary: Django phase-2 model
  loading, `ready()` signal wiring, an ASGI factory or spawned worker after
  `django.setup()`, an optional dependency, or a documented historical import
  compatibility surface. Keep typing-only imports under `TYPE_CHECKING`.
  Comment the actual reason and the point at which the import becomes safe;
  a deferral does not permit a forbidden dependency. Use the native submodule
  discovery owner to distinguish absent optional/generated modules from broken
  imports, and retain errors raised by modules that exist. The
  [addon discovery flow](../composer.md#addon-discovery) owns import boundaries.
- A pure renderer may remain a function when it transforms explicit values
  without interpreting another object's internal policy. If it decides what an
  object means or how its state behaves, move that decision to the owner. Field
  count and absence of mutation do not exempt a helper from the ownership rule.
- A package `__init__.py` whose sole job is re-exporting a stable public API is a
  compatibility surface; `__all__` is allowed there (the usual "avoid `__all__`"
  rule targets ordinary modules).
- When restructuring or lifting existing code, reconstruct each module from its
  contract, tests, and these guidelines rather than mechanically porting the old
  shape. Remove obsolete modules unless a named compatibility promise requires
  an import path; historical migration imports are one such promise.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the owning model class.
- `angee.base` declares model markers, decorators, and field projection facts;
  `angee.compose` interprets them during emission. If runtime code consumes a
  source-model structural marker, the composer carries it onto the emitted
  concrete class instead of callers inheriting or probing the abstract source.
- Field classes own data-resource classification declarations. Field authors set
  `angee_widget`, `angee_scalar_hint`, and `angee_currency_field` on the field;
  `angee.data.field_classification` reads those declarations and does
  not special-case addon-owned field classes. Custom widget keys follow the
  [frontend registry naming rule](../frontend/guidelines.md#rules); the owning
  web addon registers the identical key. Unknown bare built-in names remain
  schema errors.
- A computed GraphQL field may declare presentation facts through
  `strawberry_django.field(metadata=...)`. The shared classifiers in
  `angee.data.field_classification` own resolution: surface metadata first,
  Django field declarations second, then the ordinary type fallback. Money
  projections use `MONEY_CURRENCY_FIELD_METADATA_KEY` from its owner
  `angee.data.field_classification` instead of repeating its metadata key. Presentation metadata alone grants no
  ORM write or aggregation capability; those still come from a real model field
  or explicit resource input policy.
- Manually ordered rows use `FractionalRankField` (NOT NULL) plus a database
  `UniqueConstraint` over their context fields and rank. Use the field's
  append/between API; `FractionalRankExhausted` means enqueue
  `jobs.rebalance_fractional_ranks`, never guess an epsilon or reuse a rank.
  Rebalance context keys are model *field names*, not attnames —
  `{"project": pk}`, never `{"project_id": pk}`.
- `runtime/`, generated schemas, migrations, and codegen stubs are output.
  Change the source, not the artifact.
- REBAC is structural and owned by `django-zed-rebac`. Addons declare
  `permissions.zed` beside the owning app. Permission sync is the library's
  own `manage.py rebac sync`. Use the library's
  live ORM-backed relations when a relationship is already represented by a
  Django field, path, or membership table. See the REBAC section below for
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
    `angee.base.impl.ImplClassField`** naming a non-model
    strategy/client/backend class. Name the field by the role it plays
    (`backend_class`, `provider_type`), not by a generic "implementation" label.
    One table (unified list/reconcile, no field duplication); the impl is an
    **explicit per-row** choice, **never** derived from a vendor slug (a vendor
    can have several impls/accounts).
  - *Only behaviour differs, closed framework-known set* → a `StateField` + an
    **enum-owned handler mapping** (`integrate.credentials.CredentialKind.handler`).
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
  Optional states declare `null=True, blank=True`; absence is `None` in Python
  and NULL in storage, so native Strawberry-Django `auto` emits a nullable enum.
  Do not add blank-string sentinels or per-field GraphQL coercion. The legacy
  non-null blank constructor remains available for historical migration fields;
  [`StateField.check()`](../../angee/base/fields.py) rejects that declaration on
  concrete models, and `tests/test_layering.py` guards active source declarations.
- **Reference codes with upstream labels remain string fields.** A country code
  identifies external ISO reference data; it is not a row lifecycle state.
  `angee.parties.fields.CountryCodeField` therefore retains the GraphQL/string
  boundary while its Django choices supply selector labels through resource
  metadata. It accepts ISO alpha-2/alpha-3 codes and exact names/aliases,
  resolved through the configured django-countries catalogue first (so
  `COUNTRIES_OVERRIDE` names win) and the complete pycountry ISO catalogue only
  as an exact-match fallback; fuzzy or colloquial names fail validation instead
  of being guessed. Compose that owner for postal, tax, and bank countries
  instead of declaring another country vocabulary or using `StateField`.
- **A hand-written `@strawberry.type` owes the boundary the same enum.** The rule
  above is not about models — it is about the API boundary, so a `state: str`
  field on a plain strawberry type has the identical defect: it crosses as a bare
  `String` and pushes the vocabulary check onto every client at runtime. Declare
  the closed set as an `enum.StrEnum` exposed with `@strawberry.enum` and type the
  field as it; the member value stays the serialized/stored token and the
  upper-case member name is the wire value
  (`angee.integrate.live.PairingState` is the reference). Keep the
  enum next to the vocabulary it names, not in `schema.py`, when a worker-side
  owner must return it without importing the console schema.
- **Integration implementations are concrete integration children.** The
  top-level `integrate.Integration` row is the shared connection identity and
  lifecycle. Concrete integration kinds such as inference providers and VCS
  bridges are child models; their forms open from the integration surface and
  contribute implementation-specific tabs/related tables. A child model may carry
  its own `backend_class` when several SDK/protocol adapters share that child's
  persisted shape. Do not store a second generic `impl_class` on the child: the
  child model is the integration implementation; the backend field is the adapter.
- **A row-selected impl is stored as a registry key, never a dotted path.**
  `ImplClassField(Base)` stores a short key and resolves it against the Django
  setting named by `Base.registry_setting`; the owning addon lists `Base` once in
  `ANGEE_IMPL_REGISTRIES:append`, and contributors add paths through an autoconfig
  dotted key (`"ANGEE_…_CLASSES.<key>": "<dotted.path>"`). So a writable column
  never feeds `import_string` (the path comes from composed, trusted settings,
  like an addon's `schemas` reference), the available impls are a composition
  fact rather than a base-model import, a project can remap a key to its own
  class, and the base system check validates every listed registry's paths, base
  classes, and keys, including rowless catalogues. Because every addon has contributed by schema-build
  time the key set is closed, so the field is a `TextChoicesField` and
  `strawberry-django` renders the GraphQL enum natively (like `StateField`). It
  therefore requires a **non-empty** registry: an addon whose impl set could
  otherwise be empty registers a noop/null-object default (storage's `local`;
  integrate's `none` VCS client), so a composition always has one selectable
  impl and the enum is never empty.
- **Rowless implementation catalogues compose the same registry owner.**
  [`angee.base.impl`](../../angee/base/impl.py) owns registry checks, choice
  metadata and native enum projection for fields and settings-selected uses.
  Callers name only the base (`resolve_impl_class(Base, key)`, `impl_choices(Base)`).
  Empty catalogues may pass checks and return no choices; projecting an enum
  requires entries. The base app registers one check for every declared registry;
  keep any selected-key policy with its addon. GraphQL adapters
  live in [`angee.graphql.impl`](../../addons/angee/graphql/impl.py); callers
  retain authorization policy.
- **Hooks are declared callable settings, separate from keyed registries.**
  The owning addon appends each hook setting name to `ANGEE_HOOKS`, and
  [`angee.base.impl`](../../angee/base/impl.py) resolves its optional callable
  or ordered callable list with `resolve_hook` or `resolve_hooks`. The base check
  imports and validates every declared hook; the caller owns invocation order
  when it needs to sort contributors.
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
  The native enum and choice projections belong to `angee.base.impl`; callers
  without a model column use that owner after settings composition.
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
  importing it); add *fields onto another addon's GraphQL input*, including a
  generated Hasura input, with native
  `strawberry.input(name="UpstreamInput", extend=True)` listed in
  `input_extensions`. Input extensions are the write-side equivalent: they name the
  target input and add fields only; Strawberry merges multiple donors additively in
  addon order and fails fast on field-name collisions. strawberry-django-hasura
  forwards extension values in the resource's write data. The default
  [`AngeeHasuraWriteBackend`](../../addons/angee/graphql/data/hasura.py) writes
  values naming model fields with the row and, in the same transaction, passes
  the rest to the written row's cooperative
  [`AngeeModel.apply_input_extensions`](../../angee/base/models.py), nested line
  rows included. Its consumer (a mixin or `extends` donor on the model) takes
  each value it owns as a keyword-only parameter and delegates the rest once
  through `super()`; the terminal fails fast on a value no consumer took. A
  custom write backend consumes its own extensions: IAM's
  [`UserPasswordInsertInput`](../../addons/angee/iam/schema.py) names the
  password column, which only the person factory may hash. Resource metadata
  marks every field of the final insert/set input creatable/updatable,
  extensions included. Type and
  input extensions are global-additive, like a model `extends`: the field lands
  on the target wherever it appears (the bucket only gates registration), so
  reference a field type that some bucket lacks and that bucket's build fails
  loudly rather than leaking.
- Use symbolic model references across addon boundaries; avoid import cycles.
- Build output must be byte-deterministic.

## REBAC

REBAC is owned by `django-zed-rebac` (see the Rules entry and `docs/stack.md`).
This project runs **fail-closed**: `REBAC_STRICT_MODE=True` and
`REBAC_SUPERUSER_BYPASS=False`, so every actor — superusers included — reaches
data through REBAC, never a queryset bypass.

- **The local backend is the only supported REBAC backend.** Base
  [autoconfig](../../angee/base/autoconfig.py) selects it because field- and
  const-backed relations, `authenticated`, and SQL read scopes are evaluated
  against the Django rows themselves, which no remote tuple store can see. Do not
  mirror column facts into tuples to keep another backend viable.
- **One user identity for authorization and attribution.** Every person and
  agent acts as its own `AUTH_USER_MODEL` row. Workflows also act as their linked
  `kind=service` user for trigger admission and triggered runs; source-declared
  grants give that user its reach. The enabling user must have workflow write
  access and each target's delegation permission, but is not the runtime actor.
  At every principal run start, the pinned version's human publisher must still
  have every enabled trigger grant's delegation permission. System-installed
  versions are trusted. Trigger grant provenance retains the permission and its
  resource so admission can recheck the source owner's policy even if the source
  implementation changes. A refused trigger disables with a reason; a refused
  child start fails its step. The enable preview shows prospective grants and
  workflow monitor readers through REBAC's native subject lookup.
  A `TriggerSource` declares its grant targets; a model captured by
  `record_changed` explicitly opts in and declares its own grant targets.
  Enablement writes listable, revocable tuples to the workflow principal, not
  an implicit permission on the enabling user. Workflow monitoring grants
  readers access to triggered runs; per-reader record references remain redacted
  when their target is no longer readable.
  An agent's `kind=service` account is selected by
  `Agent.principal_subject()`; permissions and audit stamps use
  that same user. The agent's reach is its grants, independent of its owner's
  reach. Service users satisfy `authenticated` and `auth/user:*` like other
  users. `actor_user_id` converts the canonical PK subject ID to its FK type
  without a database lookup. See the glossary's Principal/Actor/Service
  account entries.
- **Model-backed authorization IDs are primary keys.** Use the native
  `to_object_ref(instance)` and `to_subject_ref(instance)` APIs; Angee models
  retain the library's `pk` identity default. Sqids are public representations
  owned by the model's public-ID field, not alternate authorization identities.
  Transport subject strings pass through
  [the public identity boundary](../../angee/base/identity.py) before reaching
  REBAC, and outputs encode the PK there. Signed tokens are transport too: a
  download token carries its issuing actor's public subject, and
  [`FileManager.for_download_token`](../../addons/angee/storage/models.py)
  re-checks that actor's `read` on every request. Tableless role anchors keep
  named IDs. Changing a public prefix or codec must never change grants.
- **Container inheritance belongs to the resource and scope owners.** A
  resource's FK relations and arrows live in its own Zed definition. A scope
  contributes additional relations and arrows through its own
  `permissions.extends.zed`; its binding writer mirrors the persisted evidence
  and reconciles edits and deletion. See
  [project bindings](../../addons/angee/projects/access.py). Binding a resource
  widens access to its contents, so the binding owner must authorize both ends.
- **Declare direct sharing once.** Models declare `rebac_grantable`; the
  [record-access API](../../addons/angee/graphql/sharing.py) dispatches bulk
  grants and revocations through the model's checked methods. Addons do not
  define private share mutations. Metadata projects the grant surface and the
  subject resource's public identity field; the API converts selected public
  subjects to canonical PK references before validating and writing grants.
- **Raise through the model's access owner.** `require_access(permission, actor=None)`
  delegates to native REBAC checks. An explicit actor stays bound to the instance;
  omitting it preserves native instance and ambient scope precedence. Verbs resolve
  attribution separately when they need to record a requesting actor.
- **Recipient discovery follows identity read policy.** IAM's user resource
  includes readable people and service users; human-only membership pickers
  use its people collection. IAM owns the group model and declares its native
  default subject relation as `member`; `to_subject_ref(group)` supplies the
  canonical subject set. Group membership accepts people and service users.
  Members can discover their groups and inspect their membership and bindings;
  platform admins can discover all groups. Group mutations enforce the group's
  write permission.
- **Roles are schema; groups are data.** Addons declare named role anchors and
  their reach. The hub derives its role catalogue from native schema
  introspection, including roles with no members. An IAM group with bindings
  is a dynamic composite role: grant its `auth/group#member` set relations on
  records and membership in declared roles. Runtime data chooses memberships
  and grants; schema remains the only source of new permission arms.
- **Relations constrain stored subjects; permissions compute their reach.** A
  role hierarchy stores a plain role subject (`relation includes: <ns>/role`)
  and expands it with `includes->effective_member`. Likewise, a resource stores
  a role relation, optionally restricted to a fixed role ID
  (`relation manager: <ns>/role:<id>`), and arrows through `effective_member`;
  never name a permission such as `#effective_member` in a relation's allowed
  subject types. Usersets backed by relations, such as `auth/group#member`,
  remain valid stored subjects.
- **Reset stored grants for the primary-key identity and relation-schema cutover.**
  This upgrade deliberately does not preserve existing REBAC grants. Stop
  application traffic and take a recoverable database backup. Before applying
  the remaining Django migrations, run `manage.py reset_rebac_grants` to preview
  the rows in both local relationship stores and their resource registry, then
  run `manage.py reset_rebac_grants --apply`. The reset command deliberately
  runs without system checks so stale grants cannot block this maintenance step.
  Apply the schema-bearing Django migrations, repeat
  `manage.py reset_rebac_grants --apply`, then run `manage.py rebac sync`,
  `manage.py bootstrap_admin`, and `manage.py resources load` before resuming
  traffic. `bootstrap_admin` idempotently restores the configured user's
  `angee/role:admin#member` grant; changing `User.is_superuser` never writes
  authorization tuples.
  The reset is atomic and leaves application rows, schema rows, and permission
  audit history intact. It removes record shares, group memberships, role
  assignments, and every other stored grant; declarative resource grants are
  recreated by the final resource load. Include `--include-demo` only when that
  deployment normally loads demo resources.
- **Membership has one store and one writer.** Use `rebac.memberships` for
  direct memberships in groups and role containers. IAM's model and hub own
  authorization and subject-existence policy; the library owns tuple
  validation, persistence and exact caveat revocation. Do not add Django group
  M2Ms or synchronize `auth.Permission`, `Group.permissions`, or
  `user_permissions`. IAM owns its group table; the contrib auth group and
  permission tables remain unused after the PK-preserving adoption migration.
- **Django login and permission backends are separate contracts.** IAM's
  authentication backend checks credentials and reloads active people. Its
  inherited permission methods grant nothing; it does not query Django
  permission tables. The native REBAC permissions mixin delegates to installed
  authorization backends without its own superuser shortcut. An additional
  backend may grant codenames through normal Django chaining, so removing the
  REBAC backend alone is not a global fail-closed guarantee.
- **Person accounts have one factory.** Actor-requested accounts go through
  [`UserManager.create_person`](../../addons/angee/iam/models.py), whose `create`
  permission owns admission; ingress without an actor uses
  `create_person_as_system` with a named reason, once its channel owner has
  required channel `write` and account `create` to enable that ingress.
  `create_user` stays the trusted bootstrap and OIDC path.
- **Managed accounts change through IAM's account verbs.** `User.set_active`,
  `rename`, `issue_password` and `reset_password` lock the row, check their own
  Zed permission, the expected revision and the shared
  `User.account_action_blockers`, then write elevated; the `account_actions`
  projection batches the same conditions. Every verb refuses a protected
  account: staff, superusers, the actor's own and effective members of
  `iam/protected:main#member`. A consumer marks an elevated role by unioning it
  into that permission from its fragment, never by mirroring holders; see
  [`tests/extcontrib`](../../tests/extcontrib/permissions.extends.zed). Generic
  updates pass `write` and `write__<field>` gates on changed fields only;
  protected accounts additionally need `administer`.
- **Read derived facts from their owner.** Native live ORM backing exposes
  user kind/activity, roster roles and selected tools without tuple mirrors.
  Platform administration is an ordinary `angee/role:admin#member` grant to a
  user or group, managed through IAM. Bootstrap explicitly creates that grant;
  `User.is_superuser` never creates or replaces role membership. Hosts may
  separately opt into the library's native superuser bypass.
  Bulk inserts and updates therefore take effect without a reconciliation
  command. Human-only proposal evaluation intersects
  authority with `iam/kind:person#active_member`, including admin authority;
  ordinary read/share grants and service requesters remain valid.
- **Rebuild derived grants after the reset.** After the primary-key reset and
  schema sync above, run `uv run manage.py resync_tool_grants` to reconcile the
  current built-in tool catalogue and its seeded grants, then
  `uv run manage.py resync_project_access` to recreate project-container
  bindings from their application-owned rows. Run these through the stack host
  described in [Checks](../checks.md#composition-and-schema); each command
  delegates to its addon owner and is idempotent. No legacy tuple evidence is
  preserved or interpreted during this upgrade.
- **Visibility and access are REBAC-native, always.** Put relations and
  permission arms on the model's zed and let the store scope reads; never stand
  authorization up with a Python provider, `visible_to` projection, or queryset
  filter. Scope roles live on the scope definition, and scoped models derive
  arms from them (`scope->viewer` for read, `scope->editor` for write).
- A `read__<field>`-gated field is never filterable, sortable, groupable, or aggregatable.
  A field gate is not creation policy: the library enforces `write__<field>` on
  update only, so keep a protected creation value out of generated insert inputs
  and let its owning verb supply it.
- **Following is notification state, never access.** Messaging's
  `ThreadFollower` is keyed by party; follow/unfollow write no relationship
  tuples. A person account follows a record only while it passes that record's
  `thread_reader_allowed` gate. Explicit add may grant read through the selected
  role owner and follow in one transaction; automatic follows skip nonreaders.
  The access owner's direct revoke or seat removal calls
  `ThreadFollower.objects.end_unreadable_for_record` in the same transaction to
  end follows that lost read. Accountless parties and service accounts remain delivery
  routes without this read condition. Unfollow leaves access intact. Explicit
  `Thread.grant_reader` / `revoke_reader` remain shares governed by `share`.
  Team and record audiences are read live through the messaging contracts;
  they are not copied into followers. See
  [`ThreadedModelMixin`](../../addons/angee/messaging/models.py) and
  [`ThreadNotificationManager`](../../addons/angee/messaging/managers.py).
- **Posture is data, not schema.** `permissions.extends.zed` fragments union
  permission arms and add no permission other than an owned field gate, so
  narrowable defaults ship as seeded tuples. Platform-wide tuple-driven visibility
  uses a const-backed singleton relation on each row (for example,
  `auth/user#directory` → `iam/directory:main`) plus a seed on that singleton
  (`iam/directory:main#reader`), never base schema arms or per-row fan-out a
  deployment cannot omit.
- **Row-dependent shared visibility uses a filtered constant.** Filter the
  resource's own columns and target its own resource type at sentinel ID
  `shared`, then arrow to `shared_reader = authenticated`; see
  [dashboards](../../addons/angee/dashboards/permissions.zed). The sentinel needs
  no target row because the arrowed permission is the authenticated builtin.
  `authenticated` admits every non-anonymous subject, so public rows also admit
  non-user subjects such as agents and integration sources that the former
  `auth/user:*` tuple excluded; this widening is intentional.
  `shared_reader` is arrow-only: never check it directly or use it as an action
  or field gate, which would bypass the row filter. Check the resource's `read`
  permission instead. Multi-table children read through their parent's permission,
  as [work queues](../../addons/angee/work/permissions.zed) do for public
  [groups](../../addons/angee/spaces/permissions.zed); no tuple mirrors the column.
- **Always-shared reference data reads through `authenticated`.** A resource
  every signed-in subject reads unions the library's `authenticated` builtin into
  `read` and stores no wildcard tuple; [`tags/tag`](../../addons/angee/tags/permissions.zed)
  is the reference. `authenticated` also admits non-user subjects.
- **Caveated relations are refused in framework fragments.**
  [`angee.E024`](../../angee/base/checks.py) rejects caveated subjects in every
  effective schema. Actor-scoped querysets carry no caveat context. Express
  row-dependent conditions as live field-backed relations.
- User-requested reads and writes retain their actor scope. Reserve
  `system_context`/`asystem_context` for named system-owned work; do not elevate
  a user factory merely because it inserts a row.
- Native REBAC `create`/`insert` evaluates the unsaved candidate's field- and
  const-backed relationships, so per-row `create` gates remain authoritative.
  Compose that path for ordinary factories, as
  [`AgentSessionManager.start`](../../addons/angee/agents/models.py) does.
  Manual factories needing an explicit relationship preflight use
  [`AngeeManager.check_create`](../../angee/base/models.py); restore the
  authorized actor after any required per-instance elevated insert.
- Model universal-admin reach as a const-backed relation
  (`relation admin: angee/role // rebac:const=admin`, no tuple or FK) resolving
  membership in `angee/role:admin`. Admin-gate a table-less/synthetic resource
  with a `managed=False` abstract anchor model (passes `rebac.E009`, emits no
  table) plus that const admin, and keep an `| angee/role:admin#member` arm in
  `member` or `rebac.W004` fires.
- **Const-backing is the one canon for tuple-free role reach.** A resource that
  grants a *named* role (e.g. `storage_admin`, `<consumer>_reviewer`) declares a
  const-backed relation to the role namespace and arrows through
  `effective_member`: `relation manager: storage/role // rebac:const=storage_admin`
  with `permission … = manager->effective_member` (mirror of `admin->member`).
  A stored, per-resource relationship may instead restrict its allowed subject
  to a fixed plain role (`relation manager: storage/role:storage_admin`) and
  arrow through `manager->effective_member`; this retains the tuple. Appending
  the computed `#effective_member` permission to that allowed subject is invalid.
  The const *target* role namespace needs its own `definition` + `managed=False`
  anchor model (like the resource's const admin), because a **non-member** check
  walks the arrow into `<ns>/role#admin`; without the anchor that const cannot
  resolve and the evaluator raises instead of returning a clean deny. Bump the
  package `@rebac_schema_revision` when migrating a def to the const shape.
- **A consumer addon contributes domain relations to another addon's definition
  additively — never by editing the target zed.** The owning addon's
  `permissions.zed` declares the *seam* (for example, the in-repo spaces addon
  extends `messaging/thread`); a consumer addon that needs its own role adds it
  from its own **`permissions.extends.zed`** (sibling to `permissions.zed`),
  owned by `angee.compose.permissions`. The `tests/extcontrib` fixture keeps the
  mechanical merge covered. Each
  `definition <target> { … }` block in the fragment names an existing definition
  and lists the relations it contributes and the permission arms it unions in
  (`permission read = <term>` merges to `read = (<base>) + (<term>)`). The
  composer merges every fragment into its target's owning package at build time,
  emits the merged effective zed to `runtime/permissions/<package>.zed`, and
  repoints that package's `AppConfig.rebac_schema` at it, so `rebac sync` /
  `rebac check` / `reconcile_permissions` all read the additive superset with no
  library change. The merge fails fast on a relation-name collision (base or two
  contributors), an arm whose permission the base does not declare, and a target
  no installed package declares; contributors merge in sorted package order.
  The only new permission a fragment may declare is a field gate,
  `read__<field>` or `write__<field>`, on a concrete column that the same
  package's donor contributes to the target model; no other package may extend
  that gate. `ModelComposition.field_gate_owners` supplies the ownership map to
  [`angee.compose.permissions`](../../angee/compose/permissions.py).
  Functional drift is caught by `rebac sync` (content hash) and `angee build
  --check` (the emitted file); the contribution is revisioned by the contributing
  addon (`@rebac_schema_revision` in its fragment, echoed into the merged file's
  `@rebac_extended_by`), so the base addon does **not** bump its revision for an
  additive extension. **Editing a framework/base-addon `permissions.zed` to name
  a domain role (for example, `<consumer>_reviewer`) is a bug** — the vocabulary
  belongs in the consumer addon that owns the concern.
- There is no `rebac_roles` command. Grant writable role memberships through
  `rebac.memberships`; change derived membership at its model field. Bulk-created
  and bulk-updated active superusers have const-admin reach immediately.
- Never `select_related` a REBAC-guarded relation into an actor-scoped queryset —
  it fails live ("loaded N rows outside actor scope") while passing unit tests.
  Resolve the field elevated by FK id under `system_context`, and verify by
  rendering the live page, not just the test.
- The relationship store has two storage modes: composed projects and bare
  `tests/settings.py` both use the FK-backed `registry` mode contributed by
  `angee.base` autoconfig. Since
  django-zed-rebac 0.14 the registry queryset storage-translates the whole read
  API — `filter`/`exclude`/`get` kwargs, `Q` objects, and
  `values`/`values_list`/`order_by`/`annotate` field names — so query with the
  natural denormalized names; instance attributes (`row.subject_id`) are
  portable too (the registry manager eager-joins them). Any read shape beyond
  that API must be verified in both modes by parametrizing
  `REBAC_LOCAL_BACKEND_STORAGE` over `registry` and `denormalized`.
- Derive operator/edge token scope from `<ns>/role:<id>#effective_member` (folds
  in role-hierarchy `includes`), never `roles_of`/`roleRefs` (a direct-grants UX
  hint that under-grants).
- `rebac sync` persists the zed into DB `Schema*` tables, and the system checks
  gate every subcommand on that persisted state — so editing the zed can deadlock
  the sync. Unstick with `rebac --skip-checks sync --force-overwrite --yes` then
  `rebac sync`; never smoke-test a zed against the shared example DB.
- The local backend compiles permissions into queries over application tables;
  it maintains no permission index. Run `migrate` and `rebac sync` to prepare
  the database and store the schema. There is nothing to rebuild or verify.
- `REBAC_DEPTH_LIMIT` bounds compiled SQL nesting and inherited permission hops.
  On SQLite (default 8), access inherited through more than eight parent hops is
  denied: scoped lists omit those rows; point checks raise `PermissionDepthExceeded`.
  Deeper hierarchies need PostgreSQL (default 16). [Base autoconfig](../../angee/base/autoconfig.py)
  owns the defaults; explicit environment values take precedence over project settings.
- If a removed or renamed definition in an otherwise composed package fails
  `rebac.E009`, run the check-free `reconcile_permissions` first; it prunes stale
  package-managed schema rows before `makemigrations` / `rebac sync` can run.
- When an addon removes its last REBAC resource, keep an empty package-owned
  `permissions.zed` with a bumped schema revision until old package-managed rows
  have been pruned; deleting the file makes `rebac sync` skip the package and
  strand stale definitions in existing databases.

## Pitfalls

Recurring failure modes, grouped by the owner to inspect. Follow the code links
and current contracts before applying a historical example to a new deployment.

- [Environment and checks](#environment-and-checks)
- [Migrations and runtime](#migrations-and-runtime)
- [Models, queries, and resources](#models-queries-and-resources)
- [GraphQL and authorization](#graphql-and-authorization)
- [Integrations and workers](#integrations-and-workers)
- [Workflow execution](#workflow-execution)

### Environment and checks

- **Localhost ports do not isolate browser cookies.** Keep the project template's
  project-scoped Django session and CSRF cookie names; two development stacks on
  different ports otherwise replace each other's login cookies.
- **Run every changed test module standalone.** A full suite's file order can
  leak concrete test models into the shared registry and mask a missing
  registration; a broad run does not replace the direct module run.
- **Django owns static test-table lifecycle.** Register concrete models before
  database setup in installed, unmigrated apps, and use pytest-django's native
  setup and transactional flush. Share source compositions through the owning
  [`resources`](../../addons/angee/resources/testing/__init__.py),
  [`workflows`](../../addons/angee/workflows/testing/__init__.py), and
  [`integrate`](../../addons/angee/integrate/testing/__init__.py) test apps.
  Import the reusable addon-owned concrete compositions from the root test
  conftest before database setup, including
  [`decisions`](../../addons/angee/decisions/testing/models.py) and
  [`messaging`](../../addons/angee/messaging/testing/models.py); decisions tests
  import those models without workflow test support.
  Framework probes declared after setup, in isolated registries, unmanaged, or
  under uninstalled or migrated
  labels use the single [`model_tables`](../../tests/tables.py) helper. It drops only
  tables it created; it never clears existing tables. Keep production code
  independent of test support.
- **Patch inherited Django manager methods on the manager class.** Pytest's
  `monkeypatch` can restore an instance patch as a bound instance attribute;
  Django's `db_manager()` copies then retain the original manager and lose their
  binding. Patch `type(manager)` and accept the manager argument in the spy.
- **A relocated virtualenv can retain stale launcher shebangs.** Diagnose the
  interpreter and environment owner when a console script cannot spawn; do not
  assume an application failure. [Checks](../checks.md) owns the supported
  commands, module invocation, and environment preparation.
- **Celery workers do not autoreload.** Only the ASGI `runserver` reloads on
  source edits. After changing task, model or settings code, restart the workers
  through the operator (`angee --root "$ANGEE_ROOT" restart celery-worker` and any
  other worker services), or run the whole-application restart; see
  [Restart the running stack](../howto/getstarted.md#restart-the-running-stack).
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
- **Each running stack needs a unique Compose project name and edge port.**
  Colliding names can merge containers into another stack's Compose project;
  colliding ingress ports can route requests to the wrong runtime. Inspect the
  rendered stack manifest and use its template-owned names and port leases.
  A source workspace inside one stack does not itself imply a second running
  stack. Resolve the lifecycle owner through the workspace workflow before
  changing stack or service templates.
- **Addon moves can change the workspace dependency graph and historical imports.**
  Use [checks and dependency preparation](../checks.md) to select the owning
  environment. In a materialized workspace, run a needed `pnpm install` only at
  the owning stack root, never in this source slot. Preserve migration imports
  using the [migration rules](#migrations-and-runtime); a gitignore entry does not
  make a migration disposable.
- **Never name an addon module after a third-party top-level package it imports.**
  `unittest` discovery inserts the discovery-root directory onto `sys.path`, so an
  addon's `mcp.py` that does `from mcp.server… import …` becomes an importable
  top-level `mcp` that shadows the real package — `ModuleNotFoundError: 'mcp' is not
  a package` during a test run, while a single-module run and `manage.py check` pass.
  Name such a module for its role, not the library (the MCP tool seam infers — or
  resolves a `[mcp].tools` override to — `mcp_tools.py`, not `mcp.py`).

### Migrations and runtime

- History snapshots retain stored values even when the actor's view is redacted.
  [`ModelHistory`](../../angee/base/mixins.py) gives native history construction a
  detached snapshot and records deletion inside the delete transaction before
  the source row disappears; it never unredacts the caller's instance.
- **Review local-only rows before upgrading pull-only record sync.** The
  [record-sync driver](../../addons/angee/integrate/README.md) may create them
  remotely as soon as the first baseline completes. Remove rows that must remain
  local from the synchronized scope before enabling two-way sync.
- [`HistoryMixin`](../../angee/base/mixins.py) excludes `GeneratedField` and its
  subclasses from historical models because their expressions belong to the live
  row. Base autoconfig enables the native
  `SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD` setting, so simple-history
  allocates a separate nullable text change-reason field for each historical
  model; inherited tracking must never share mutable field instances across models.
  This is an intentional project-wide default, including consumer and third-party
  `HistoricalRecords` declarations. Existing histories that used the default
  `CharField(max_length=100)` need a schema migration to `TextField`; declarations
  with an explicit `history_change_reason_field` retain their chosen field.
- **Domain renames need an explicit upgrade path.** When persisted references or
  permission namespaces change, describe which old state needs data migration
  and which reconciliation follows it. Keep those operations out of startup.
  Consumers still carrying the historical `social/*` permission namespace need
  a planned transition to `posts/*`; verify their actual data and migration state
  before selecting the migration and `reconcile_permissions` steps. A fresh
  installation does not inherit an old deployment's repair procedure.
- **Convert optional-state sentinels when upgrading existing databases.**
  The declared [storage transition](../../addons/angee/storage/runtime_migrations/smart_kind_nullable.py)
  removes affected checks, makes the column nullable, converts empty strings to
  NULL, and restores the frozen target constraints. Build materializes this guarded,
  reversible migration before downstream schema autodetection. Preserve retained
  rows and historical migration bodies; a generated schema alteration alone does
  not perform the data conversion. Regenerate SDL and client types after migration.
- **Row updates followed by DDL on the same tables need immediate constraints.**
  PostgreSQL queues deferred foreign-key checks for rows a migration updates and
  refuses a later `ALTER TABLE` or index build in the same atomic migration.
  Run `SET CONSTRAINTS ALL IMMEDIATE` (PostgreSQL only) before the data step.
  SQLite never shows this; rehearse such a migration against populated PostgreSQL.
- **A structural marker consumed after runtime emission must be emitted too.**
  A non-inherited `__dict__` source-model marker stops at the abstract source unless
  the composer carries it into the concrete runtime class body.
- **Regenerate the SDL after `angee build`** — re-run `manage.py schema`
  (+ `--check`). A missing `runtime/schemas/*.graphql` makes Vite ENOENT and the
  SPA silently fails to mount (every e2e fails at list load) while `:5173` still
  returns 200; check `runtime/schemas/` before chasing app/test regressions. (The
  dev server regenerates it for you — see the `runserver` pitfall — but a manual
  `angee build` outside `angee dev` still needs the explicit `schema` step.)
- **Moving a custom field between modules changes its migration `deconstruct()` path.**
  Search source and consumer migration histories before moving it. Preserve
  released/applied migration imports with a narrow compatibility alias where
  needed; [`angee.base.fields`](../../angee/base/fields.py) preserves the historical
  `ImplClassField` path this way. New migrations use the new canonical path.
  [`angee.base.historical_relationships`](../../angee/base/historical_relationships.py)
  is frozen compatibility code for materialized historical migrations only;
  production callers must use current owners.
  Only unreleased, unapplied migrations whose consumers are known may be edited
  as part of the move. Rebuilding generated model sources does not authorize
  rewriting or deleting a deployment's migration history.
- **Framework runtime-migration history is carried forward.** Existing stacks
  must first build and migrate at the upgrade floor: django-angee source revision
  [`0a55a6fb6c249106d2f5d1407cd82865bb3175f8`](https://github.com/ang-ee/angee-django/commit/0a55a6fb6c249106d2f5d1407cd82865bb3175f8),
  which still carries the 42 retired addon migration declarations. This ensures
  applicable declarations have been materialized and applied before upgrading.
  Preserve those files and generate incremental migrations after the next build;
  [`RuntimeMigrations`](../../angee/compose/migrations.py) preserves existing
  materialized bodies when their declarations are removed.
  The composer now registers retained migration-only labels even when their source
  addon is absent, including `workflows_ocr`. This keeps dependencies loadable but
  does not restore historical Python imports or authorize data retirement. Verify
  those imports and the owner's declared forward cutover against the recorded graph.
- **Never empty `runtime/*/migrations` on a stack whose database is carried forward.**
  Gitignored migrations can still be applied history; recreating their names or
  numbering can cause Django to apply existing schema again. Durable deployments
  retain and version runtime migration history with their deployment artifacts.
  Investigate the recorded graph before recovery; blanket migration deletion and
  `--fake` must not hide a mismatch. A consumer repository must explicitly
  authorize any reset of its own labels on a rebuilt database; framework upgrades
  do not authorize a reset.
- **Fresh-stack reset debt (Decision 29).**
  [`Runtime.clean_configured`](../../angee/compose/runtime.py) preserves migration
  history by policy. There is no owner for resetting generated migration history
  together with a throwaway stack, so those stacks currently delete generated
  runtime migrations by hand. The fix needs a stack-owned reset operation that
  proves the stack and its database are disposable before removing that history;
  the preservation policy blocks using `angee clean` for this purpose.
- **Data migrations access REBAC-scoped models through `_base_manager`, and
  backfills need a rows-present proof.** A manager with `use_in_migrations = True`
  (iam's `UserManager`, inherited from Django's) rides into the historical model,
  so `objects` inside a `RunPython` is REBAC-scoped and raises `MissingActorError`
  under strict mode — a migration is a system operation; use
  `model._base_manager.using(db)`. And a fresh-DB `migrate` never executes a
  row-dependent backfill body: prove backfills against a database that has rows
  (the agents service-user backfill failed only on live dev DBs for this reason).
- **Addon-owned runtime migrations are append-only, self-contained history.**
  [The composer migration owner](../composer.md#addon-owned-runtime-migrations)
  defines declaration and materialization behavior. Guards select the exact old
  state, skip complete new or absent states, and reject recognized partial states.
  Copy-local `RunPython` functions use historical models from `apps` and
  `_base_manager`; clear ordering before writes when it can name live-model alias
  fields such as `sqid`. Ship a new declaration for a new transition and preserve
  materialized bodies. Source compatibility exceptions require the owner's
  explicit historical-digest mechanism and verification of both old and new
  histories; they are not permission to rewrite copies. Keep formatter exclusions
  for `**/runtime_migrations` because formatting also changes the pinned digest.
- **Removing a field or donor addon whose column holds data needs a declared
  cutover.** `makemigrations` refuses the autodetected drop through the
  [composer's drop guard](../composer.md#addon-owned-runtime-migrations). An
  addon that stays installed declares a runtime migration that preserves or
  retires the data and removes the field; an empty column needs nothing.
- **A restricted `makemigrations` invocation must cover every changed concrete
  app.** Derive labels from the composed model registry instead of copying an
  old example's label list. Missing a changed app's migrations can leave its
  tables absent when resources load. Use [checks](../checks.md) for the host
  lifecycle sequence and execution root.
- **`MIGRATION_MODULES` may be assigned during app populate only for generated
  runtime apps.** That exception belongs to composed settings/runtime boot; do
  not use it as an addon-local shortcut or a way to hide source-model migration
  state.

### Models, queries, and resources

- **Group counts add to root totals only for disjoint populations.** Reuse the
  grouped scan with a window total when every record belongs to one bucket;
  recipient fan-out and shared content require distinct root counts. Hydrate
  labels after paging unless they determine group identity or sort order.
- **`.values_list(...).distinct()` must clear the model's default ordering.**
  `Meta.ordering` columns silently join the DISTINCT projection, so a
  single-column `values_list("owner_id").distinct()` returns one row per
  *source row*, not per owner — a loop over it repeats its whole body once per
  row (a 4-hour beat tick that should take a second, live-measured). Append
  `.order_by()` (or order only by the selected columns) before `.distinct()`
  on every values/values_list distinct read.
- **Seeded rows selected by clients carry a resource-assigned stable key.**
  Select them by that stable key, never by a mutable display name.
- **Parties bookkeeping follows its writer's transaction contract.** IAM's person
  factory links the party inside its own transaction by design:
  [`person_created`](../../addons/angee/iam/events.py) fires before commit, so a
  linkage failure rolls the account back. Foreign writes that only enrich parties
  treat that work as best effort: ingest defers it with `transaction.on_commit`,
  OIDC login contains its handle claim, and both log failures and let the foreign
  write continue.
- **IAM stores the canonical person email.**
  [`UserManager.normalize_email`](../../addons/angee/iam/models.py) owns the rule,
  and saves that write `email` apply it, so lookups and the person-email unique
  constraint compare the stored column without SQL transforms. `bulk_create` and
  queryset `update()` bypass it; normalize before writing email through them.
- **Polymorphic edges write at the canonical MTI level.** Route their targets
  through `angee.base.canonical_record_target`; compose `ThreadedModelMixin` and
  reverse `GenericRelation`s on that same canonical ancestor. The edge owner
  authorizes that canonical target: [`FileAttachmentManager.attach`](../../addons/angee/storage/models.py)
  requires its `write` and fails closed, outside system context, for a target
  without a REBAC type.
- **Derived columns have two drift classes and two owners.** Signals own instance
  saves/deletes, cascades, and queryset deletes; idempotent repair passes own
  `bulk_create` and queryset `update` paths, where signals do not run.
- **`ScoredLinkMixin` is the scored-suggestion shape, not a permission owner.**
  A subclass that needs REBAC side effects overrides the transition; never add
  REBAC writes to the shared mixin.
- **State columns are `StateField`; guarded changes go through transition methods, never direct assignment.**
  [`StateTransitions`](../../angee/base/transitions.py) owns one transaction around
  the body and success hook, including `save_state`; consumer outer transactions
  compose through Django savepoints. Since the body runs inside that transaction, follow the
  [two-phase side-effect rule](#rules): defer non-database effects to
  `transaction.on_commit(...)` or a post-commit phase.
  Save guards use `get_transition_save_field(instance)` to read the active save
  field's attname, or `None`, through the public contract.
  Guards follow Django's final concrete fields, including inherited and deferred
  columns. See [seeded transition state](#seeded-transition-state) for initialization.
  Reload committed state through `AngeeModel.refresh_from_db`, or copy loaded
  values from the owner's persisted copy of the same row through
  `StateTransitions.copy_persisted_state`; reload authorization stays private.
  Recovery writes outside the graph use `force_state` with a concrete reason.
  Compose a custom final save through `persist(instance, *, update_fields)`;
  the success hook must explicitly forward it to `save_state`, which retains the
  concurrency guard and transaction.
- **MTI down-casts use `angee.base.refs.concrete_child`.** The parent-link
  accessor and any query prefetch identify the child; callers supply their
  actor-scoped queryset when that lookup is permission-sensitive. A parent
  row never grows a human-label kind column to mirror its child model.
  Clients read a parent's exposed children from its resource metadata's
  `concrete_kinds` ([projection](../../addons/angee/graphql/data/metadata.py))
  instead of re-deriving inheritance.
- **Integration children use the ordinary emitted Django MRO.** The composer
  emits donors, the child's abstract source, then its concrete parent, so child
  behavior can override parent behavior and cooperative methods delegate with
  `super()`. A verb starting from an `Integration` parent row must still resolve
  the concrete child before dispatch because Django does not downcast multi-table
  parent instances automatically (`Integration.concrete_capability` composes the
  shared child lookup). Walking
  `models_with(base=Bridge)` fans a query across every installed bridge table, so it is not
  free.
- **Instance `save()`/`delete()` overrides do not run on cascade or bulk queryset paths.**
  Lifecycle guards and side effects that must survive those paths belong on Django
  signals calling the owning model rules. Agents' teardown and active-turn guards
  use `pre_delete`; service-user deactivation uses `post_delete`. See the
  [agents receivers](../../addons/angee/agents/signals.py).
- **Deletion refusals have one model owner.** Override
  [`AngeeModel.delete_blocker()`](../../angee/base/models.py) with a public-safe
  message. [`DeletePreview`](../../addons/angee/graphql/deletion.py) reports each
  distinct refusal before deleting, without row counts or hidden identities.
  A model overriding the hook must bind a `pre_delete` receiver enforcing that
  same rule on every deletion path; an instance override cannot protect cascades.
  Receivers remain authoritative when state changes after preview and ensure
  Django collects those models rather than fast-deleting them.
  When deleting a model needs locks in a domain order (a parent before the row),
  override its [`lock_for_delete()`](../../angee/base/models.py); its `pre_delete`
  receiver and every confirmed-delete caller of that model then lock through the
  hook, callers only after their permission preflight.
- **Never a database trigger or function.** Business rules, immutability and
  ownership guards belong to Django owners: cover instance, queryset, bulk,
  cascade and relation writes in the owning models/managers/querysets, with
  explicit Django signals where relation writes bypass those owners. Keep
  declarative constraints (`UniqueConstraint`, `CheckConstraint`) and portable
  row locks. Forbidden: `CREATE TRIGGER`, `CREATE FUNCTION`, plpgsql, `RunSQL`
  that installs them, per-backend mirrors of them, and system or deploy checks
  that verify a trigger exists instead of the rule holding. A trigger is a
  second, invisible owner of a rule that the ORM cannot see, test, or migrate
  from models. Raw SQL is not a supported business-write path. On encountering
  existing trigger machinery, delete it and move the rule to its Django owner
  in the same change.
- **Write rules use their Django owners.** Keep persistence policy at the
  level that owns the invariant; do not add a framework-wide operation,
  payload, token, or consumption protocol.

  | Concern | Owner |
  |---|---|
  | Aggregates and multi-row writes | Explicit domain manager methods |
  | Row invariants and transitions | Model methods |
  | Atomicity and concurrency | `transaction.atomic()`, `select_for_update()`, and conditional updates |
  | Uniqueness and row consistency | Database constraints on the model |
  | Forbidden bulk operations | Explicit overrides on that domain's queryset |
  | Retention and deletion | Deliberate FK policies and Django's deletion lifecycle |

  Use `PROTECT`/`RESTRICT` for retained rows. Collector writes bypass model hooks,
  and Django may apply an unevaluated `SET_NULL` or `SET_DEFAULT` field-update
  queryset through its public `QuerySet.update()` path. Queryset and
  instance deletion overrides do not protect against every collector path; choose
  FK policies deliberately, including generic relations that can cascade into
  retained rows. This is a modelling rule, not a mechanical system check. Shared
  retained models compose [`AppendOnlyModel`](../../angee/base/mixins.py) and
  their default managers compose `AppendOnlyQuerySet`. The model closes ordinary instance
  writes and declares a guarded base manager through `Meta.base_manager_name`;
  `angee.E034` checks its default manager's queryset composition. The queryset
  rejects generic updates and deletion with a model-labelled `ValidationError`.
  Its `validate_insert` seam narrows all generic insert entrypoints. Retention
  commands insert validated batches through `owner_bulk_create`; lease state
  machines expose exact conditional writes through domain queryset methods using
  `owner_update`. These public, framework-protected APIs skip only the declaring
  owner's guard and retain downstream authorization and queryset guards. Each
  owner supplies its validated predicates and allowed fields. `HierarchyQuerySet`
  exposes the same `owner_update` contract for derived path maintenance. They do
  not reopen generic mutations or replace model/FK invariants. `AuditMixin` uses its serializable
  `retained_set_null` FK policy to materialize the collector selection and schedule
  Django's native `UpdateQuery.update_batch` path for actor deletion.
  Bounded diagnostics and persisted model labels use `DiagnosticTextField` and
  `ModelLabelField` from [`angee.base.fields`](../../angee/base/fields.py), so
  instance saves and queryset writes share their storage rules.
  **Messaging field adoption debt:**
  [`MessageSubtype.model_label`](../../addons/angee/messaging/models.py) and
  [`ThreadNotification.failure_reason`](../../addons/angee/messaging/models.py)
  still use plain Django fields. Adopt `ModelLabelField` and
  `DiagnosticTextField` at those models after live-data migrations preserve and
  normalize existing values; the required migration is the blocker.
  Never replace these rules with a database trigger or function.
- **Resource imports use the native import-export lifecycle.** Models composing
  [`ResourceLoadMixin`](../../addons/angee/resources/mixins.py) may declare
  an `AngeeResource` subclass through `resource_class`; [`build_resource`](../../addons/angee/resources/loader.py)
  composes it with native identity loading, validation, row results and the single
  ledger hook. Domain adapters may defer persistence to a manager without
  inventing another importer or ledger API. A batch preflight may acquire only
  the complete ordered domain lock set; it must not consume groups or write rows.
  `AngeeResource.resolve_existing` resolves declared targets and retained ledgers
  through the same native instance loader; preflights compose this public owner.
  `Resource.objects.load_xref` imports one declared dotted xref through that
  pipeline, preserving widgets, the demo-tier guard, and caller authorization
  before updating existing targets and on unchanged replays. It does not import
  other rows or prerequisite targets.
  A `RecordRefMixin` import column uses the model's single `GenericForeignKey`
  name; that field also owns the content-type and object-id backing column names.
  Source omission and explicit null must remain
  distinguishable through dataset normalization.
- <a id="seeded-transition-state"></a>**Transition-owned state in a seed is an initial value, applied on create and never on update.**
  Seed initial state through model construction.
  This covers only fields guarded by a
  [`StateTransitions`](../../angee/base/transitions.py) declaration; the
  [resource loader](../../addons/angee/resources/loader.py) validates seeded state
  before discarding it on updates. Companion fields written by transitions
  (an agent's `workspace`, `service`, `runtime_status`, `last_error`, or receipts
  such as `submitted_at`) remain ordinary seed fields and are not protected.
  Seeds must omit those fields to preserve their live values.
  Unchanged rows with hashes from the previous state-inclusive rule migrate
  only their ledger hash; changed seed values or keys still trigger import.
- **A resource yaml loads only when listed** in the addon's `addon.toml`
  `[resources]` manifest (`{tier = [paths]}`); an unlisted file silently
  loads nothing.
- **Demo resource tiers are additive across installed addons.** A deployment that
  needs demo rows but not one permissive seed must exclude that exact entry with
  `ANGEE_RESOURCE_EXCLUDED_ENTRIES`, keyed as `addon.name:resources/path.yaml`;
  do not edit the addon seed or fork the loader.
- **Give a model an opaque public id by mixing in `SqidMixin` and declaring
  `sqid_prefix = "abc_"`** — the one fact that varies per model. The shared
  `angee.base.fields.SqidField` reads that prefix in `contribute_to_class`; don't
  re-declare the column. The field is NULL-safe by design, because a sqid can be
  selected through a nullable join where `django_sqids.SqidsField` crashes on a
  NULL (REBAC `// rebac:field=` arrows run over nullable FKs).
- **Row locks must keep the SQLite floor.** `angee.base.scoping.lock_if_supported()`
  names lock intent for both native querysets and the Angee queryset/manager
  method, delegating `of`, `skip_locked` and `no_key` to `select_for_update`.
  Django owns write routing and backend feature checks: SQLite emits no lock SQL,
  including when `of` is requested. Backends
  supporting row locks still enforce their supported lock options. Do not set
  private queryset write state or duplicate Django's feature checks.
- **A `HierarchyMixin` consumer declares its scope fields — the mixin never probes
  by column name.** A subtree that must stay inside a tenant or other scope
  declares `hierarchy_scope_fields = ("scope",)` (a `ClassVar` tuple; FKs compare
  by stored id); the mixin rejects a reparent or create under a parent that differs
  on any listed field. It is generic and iam-free — there is no scope-field-name
  fallback, so a scoped tree that omits the declaration silently accepts a parent
  outside its scope. Compose `HierarchyQuerySet` before every other queryset
  guard, including on secondary managers; the base `angee.E021` check rejects
  ordering or `update` overrides that path maintenance would bypass.
  `StateField` transitions guarded by `save_state` get an
  optimistic-concurrency guard for free: the committed source is re-read under the
  same lock before the write, so a lost race raises `TransitionNotAllowed` instead
  of double-applying (e.g. double-posting a ledger).
- **Django 6 refreshes `F()`/expression fields back onto the instance via
  `UPDATE ... RETURNING` before `post_save`.** A `save(update_fields=…)` whose
  fields hold expressions (`F("count") + 1`, `Greatest(…)`) leaves the instance
  carrying the DB-true resolved values, not the expression objects — the
  `post_save` receiver (and any `changes` publisher) sees the true row. Never
  "restore" a locally recomputed value (`prior + 1`) after such a save: it stomps
  the RETURNING value and undercounts whenever a concurrent write advanced the
  column further.
- **`AngeeModel` managers/querysets must keep the canon.** If a model customizes
  `objects`, its queryset class must derive from `AngeeQuerySet`; otherwise
  shared methods such as public-id lookup, actor scoping, and elevated reads drift
  between models.
- **`EncryptedField` keys are bound to `model._meta.label_lower` plus field
  name.** Renaming a model/app/field changes the derived key. Plan
  `ANGEE_FERNET_KEYS`/`MultiFernet` rotation before such a rename, and treat one
  corrupt row as a row-local unreadable value, not as a reason to break list
  queries.
- **An `ImplClassField` builds its enum at model-import time from its base's
  `registry_setting`** — the key→path mapping (e.g. `ANGEE_STORAGE_BACKEND_CLASSES`)
  is supplied by the owning addon's `autoconfig`, so every settings module that
  installs the addon must carry a **non-empty** mapping. Bare settings modules
  call [`AutoConfig.apply_installed()`](../../angee/compose/autoconfig.py) before
  Django loads models, then add only their fixture implementations. An empty
  registry raises `ImproperlyConfigured` at import — give the addon a
  noop/null-object default so the set is never empty. The column stores the key
  (`local`), never a dotted path. The one bounded exception is a deconstructed
  historical migration field: migrations intentionally omit `base_class`, so it
  may reconstruct its declared default after the registry has been removed. This
  exists only to replay and remove old columns; active model fields still require
  a typed base and a non-empty registry.
- **Implementation subclasses must replace every inherited semantic default that changes.**
  See `ImplBase.effective_defaults()` for the merge contract. An OpenAI-compatible
  backend that omits its own `name` and `vendor` silently creates an OpenAI provider row.
- **Pydantic declarations own implementation config defaults.**
  [`ImplBase.config_defaults()`](../../angee/base/impl.py) reads static input
  suggestions from Pydantic's validation JSON Schema independently of FormSpec
  support; forms project the same declaration downstream. Dynamic factories
  resolve through `parse_config()` at runtime; `normalize_config()` serializes
  that parsed model with its wire aliases. Typed implementation values share
  [`get_type_adapter`](../../angee/base/validation.py)'s cached native adapter
  and Django field-path errors. Explicit
  config parsing rejects non-empty values without a model; model-row validation
  still leaves undeclared legacy config alone. Generated choice metadata stays
  deterministic.
- **JSON Schema policy belongs to the shared base owner.**
  [`angee.base.jsonschema`](../../angee/base/jsonschema.py) checks declarations
  and local references before value validation, asserts supported formats, and
  translates errors for Django callers. Compose its bounded schema algebra;
  keep graph and binding policy in the workflow definition.
- **Actor-scoped scalar subqueries and keyset cursors belong to `angee/base`.**
  The `Coalesce(Subquery(related.with_actor().scoped().filter(pk=OuterRef).values(v)[:1]), "")`
  shape and the signed `(order_at, pk)` cursor pager are framework primitives;
  do not copy them into another addon's queryset.

### GraphQL and authorization

- **GraphQL authorization tests include a non-admin reader.** Admin-only tests
  neither pin deny-hard-fail behavior nor expose a leaked `sudo()` scope.
- **`hasura_model_resource` create `full_clean`s the input, so model + input defaults must agree.**
  Strawberry prepares one instance, calls `full_clean()`, and passes that same
  instance to the manager's `insert()` (or native insert-only save when absent).
  Factory invariants needed by both ORM and GraphQL creation belong on the
  queryset's cooperative `insert()`, which ordinary `create()` also composes.
  Model-owned defaults belong in `clean()` and `save()`; see
  [`Cadence`](../../addons/angee/nexus/models.py). A required field defaulted by
  `clean()` uses `blank=True` so Django's preceding field validation can defer
  the missing value to that owner; the database column remains non-null.
  Two input traps follow. (1) A `JSONField(default=dict)`
  (or `default=list`) needs `blank=True`: Django counts `{}`/`[]` as blank, so a
  `blank=False` container default fails `full_clean` ("cannot be blank") on every
  create. (2) An optional create-input field over a **non-null** column must
  default to `strawberry.UNSET`, never `None` — `None` is submitted as an explicit
  null that overwrites the model default (e.g. `status`/`config`), and
  `full_clean` then rejects the null. Mirror this for any new
  `hasura_model_resource` input.
- **A `strawberry_django.field(only=[...])` hint must list every column the resolver dereferences.**
  Include columns read by shared properties the resolver delegates to; otherwise
  selecting that field alone can defer-load the missing column per row.
- **Type extensions retain native optimizer hints.** Declare hints on the contributed
  field; [`AngeeSchema`](../../addons/angee/graphql/schema.py) exposes the composed
  definition to optimizer consumers without mutating shared addon declarations.
  Exercise narrow list selections at multiple row counts, including named schemas
  with different extensions.
- **Explicit delete preflight plus elevated destructive work must test both branches.**
  Storage's soft-delete path and messaging's threaded-record delete path check the
  public `delete` permission themselves, then run the owned destructive work under
  `system_context`; the library's denial-audit signal is skipped on the explicit
  deny branch by design, so add a deny-path regression whenever you use this shape.
  Do not hide independently-authorized `on_delete=CASCADE` children under an elevated
  parent cascade; dependent rows must derive delete through the parent in their own
  zed relation.
- **zed exclusion binds loosest.** Parenthesize `(a - b) + c` when combining
  exclusion with union.
- **A status field is read/write-asymmetric** — GraphQL serializes it on read as
  the uppercase enum NAME (`ACTIVE`) but the writable `Patch.status` `String`
  takes the lowercase model value (`"disabled"`). This holds inside F6 nested line
  inputs too: a child enum/choices column is a `String` on the line insert input
  (write the lowercase value), while the child node projects it as an enum (read
  UPPERCASE); an M2M child column is `[ID]` (public sqids in and out).
- **F6 line-cell metadata is projected from the final child node and nested
  input surfaces.** `HasuraLines(node=…)` declares the child node owner; after
  schema composition, its executable GraphQL types supply enum values,
  relation/list targets, accepted inputs, and required inputs. Input-only fields
  retain their Django relation and widget semantics with `readable=False`.
  Expose enum and M2M line cells on the child node so their complete read shape is
  present in the final schema.
- **Intersect write-only fields out of the read/return selection** — a field
  absent from the SDL read type (e.g. `password`) makes the detail query invalid
  and the form loads blank if it is selected.
- **Server-owned fields are excluded from the write surface, never merely
  `readOnly` in a form.** A column the server owns (audit, derived, or
  default-only) must be left out of the resource's `insertable`/`writable` set so
  it never enters the generated input type. Marking the form control `readOnly`
  only hides the widget: the field still rides the input, and the form's
  `Field.defaultValue` seeds and submits a value for it, so the client can write a
  column the server owns. Resource-level exclusion is the one authorization gate;
  `readOnly` is presentation, not authorization.
- **Validation surfaces two ways** — Django `ValidationError` flows through
  `extensions.validationErrors` (keys follow the schema naming, snake_case on Hasura resources), but GraphQL input-coercion errors
  fire before resolvers and never reach it, so guard required inputs client-side
  from `rootFields.requiredCreateFields`.
- **In test-client logins pass the backend** —
  `force_login(user, backend="angee.iam.auth.ModelBackend")`; the default backend
  order is chosen for runtime authentication concerns and may not be the session
  reload backend a focused test wants.
- **Login throttling belongs at the IAM auth seam.** Do not add per-view or
  per-test throttles; IAM composes `django-axes` at the `authenticate(request=...)`
  backend/signal path, so the password GraphQL mutation stays a thin caller.
- **A gated factory that uses `sudo()` must restore the actor before returning.**
  Elevated writes may be necessary to create the row, but callers continue under
  the original actor. Capture `current_actor()` before the elevated block and
  rebind the returned instance with `.with_actor(actor)` after save.
- **Publishers wire during `angee.graphql` app `ready()`, not schema build or
  schema import.** GraphQL schema modules declare subscription surfaces;
  `GraphQLSchemas` connects publishers from declared `changes` metadata after
  model population without constructing schemas. Readable-field projection is
  resolved only when an observable partial update needs changed values, through
  that schema owner. Building a schema never mutates process-global signal state.
- **Validate every named schema before deploying writer processes.** The GraphQL
  addon's [Django system check](../../addons/angee/graphql/checks.py) builds every
  named schema through `GraphQLSchemas` and reports construction failures. It runs
  in ordinary `manage.py check` and commands requesting the default system checks.
  ASGI WebSocket routing at application boot and the `schema` command also demand
  schema construction; MCP's resource-tool check validates its console contract.
  Run the default checks against the deployment's code and settings before
  starting writer tiers. Bare app population and `manage.py --help` stay
  build-free. Writer-only processes that call `django.setup()` without running
  checks deliberately retain lazy construction: their first observable partial
  update builds the required schemas synchronously, including inside an open
  transaction. This one-time latency keeps generic writer boot cheap; later
  schema and model-projection cache hits do not acquire the build lock. A failed
  check in another process does not prevent a writer from starting, and checking
  a deployment does not warm another process's cache.
- **Resource metadata is finalized from each named schema once.** A
  `HasuraResource` remains the native owner of its generated roots, types, and
  readable/writable field surfaces. Addon surfaces contribute that native
  reference plus only explicit Angee policy that the composed schema cannot
  recover, such as curated group axes, subtitle paths, editable lines, row
  model, and change/revision capabilities. `GraphQLSchemas` builds the complete
  Strawberry schema, projects one neutral `DataResourceMetadata` per model from
  its graphql-core schema, and attaches that same tuple for `resources()`, MCP,
  publishers, and serialized artifacts. Declare resource behavior through the
  existing Hasura/Pydantic resource and authored-root helpers; do not construct
  partial resource descriptions for later reconciliation.
- **Metadata callers consume the built schema's finalized descriptions.**
  [`GraphQLSchemas`](../../addons/angee/graphql/schema.py) owns schema lookup and
  attaching the finalized payload;
  [`angee.data.metadata`](../../angee/data/metadata.py) owns the transport-neutral
  declarations. Declare aliases and exclusions through Pydantic and compose its
  JSON serializer (`as_wire` dumps with `mode="json"`, so non-JSON leaves in
  `Any`-typed fields become strings or lists at build time and an
  unserializable leaf fails at schema build, not at encoding); do not maintain
  a recursive metadata serializer or turn descriptions into persistent models. Do not restore a separate snapshot/merge
  pipeline that reconstructs partial resource descriptions or validates
  selections independently of the composed schema.
- **A custom model value field registers its GraphQL wire type when its field
  module imports.** Call `angee.graphql.field_types.register_field_type()` beside
  the field declaration. `GraphQLConfig.ready()` imports schema declarations to
  discover publishers before later app `ready()` callbacks run. Deferring final
  schema construction does not defer those declarations, so registration from a
  later callback remains unsupported and can leave Strawberry's exact-class
  `auto` lookup unconfigured.
- **Change events read through the row unless the model declares another read
  anchor.** A target-derived child or polymorphic edge may implement
  `change_read_resource()` and return the `ObjectRef` whose `read` permission
  governs the event. The publisher captures that anchor before deletion, so
  create, update, and delete all use the same authorization boundary.
- **Data-resource field widgets are backend-owned vocabulary.** Add or rename
  widget keys in `angee.data.field_classification` with the matching
  frontend renderer; resource callers declare fields, not ad hoc widget strings.
- **Scoped feed roots share payload construction.** Pass an authorized domain
  queryset to Messaging's feed payload factories; root membership and search
  predicates remain with their querysets.
- **Payload envelopes share a base.** Repeated `error`/`error_code`, counters and
  thread-state fields across mutation payloads want one base type and one
  projector. Reuse `angee.graphql.actions.ActionResult` and `action_guard` when
  their error contract fits; changing an existing error envelope is a separate
  API migration, not a mechanical refactor.
  In-band refusals preserve the error's declared `DomainError` or `ValidationError`
  code in nullable `ActionResult.code`, with `null` when the error carries none.
- **Domain refusals are typed.** Raise a subclass of
  [`DomainError`](../../angee/base/errors.py) with a stable `code`; the
  [GraphQL sanitizer](../../addons/angee/graphql/schema.py) maps non-validation
  refusals by type to that code without detail. Never add a domain code to the sanitizer's
  expected-code allow-list.
- **Resolvers never inspect `info.selected_fields` to choose annotations.** A sort
  alias that needs an annotation is declared on `hasura_model_resource`. Lazy
  preparation belongs in `strawberry-django-hasura` at its resolved `order_by`
  boundary; extend that owner rather than re-walking selections in Angee.
- **Public-id lookups preserve their authorization boundary.** Use
  `require_instance_for_id` for required reads with the original queryset.
  `resolve_action_target` and IAM's `user_from_public_id` elevate lookup and are
  not interchangeable with readable queries.

### Record sync

[`angee.integrate`](../../addons/angee/integrate/README.md) owns the shared record
protocol. Backends declare independently ordered stream partitions; domain
managers retain identity and ingest policy. Event feeds are append-only and
idempotent by domain identity, so they never create replica links. Mutable record
replicas retain a remote and local comparison base on each link.
Replica hashes come from adapters; event feeds deduplicate through domain
identity and are never payload-hashed. Cursors contain plain finite JSON,
validated at the driver boundary.

- **The cursor commits with the records it covers.** Extract outside the database
  transaction; commit the applied page, its quarantine and the stream cursor in
  one transaction. Semantic, field-validation and record-level database data
  failures use savepoints and the existing discrepancy owner so later records
  continue. Domain write owners truncate source display text to model-field limits;
  identifiers stay lossless or are refused, never silently truncated.
  Infrastructure failures roll back the page. Conditional remote writes happen
  outside database transactions and are
  reflected only after their response; no cross-system atomicity is implied.
- **Compare both sides with their last applied bases.** Unchanged pairs do
  nothing, remote-only changes apply, and local-only changes may write back with
  the expected remote version. Both changed, including a remote tombstone against
  a local edit, is an unresolved conflict: never silently choose a winner. The
  adapter locks and revalidates its local projection before applying. An origin
  stamp plus the returned version/hash makes a successful write-back recognizable
  on its next pull.
- **Applied evidence has one promoter.** Adapters return the actual mapped
  payload, mapping version and dependency digest in `ApplyResult`; the driver
  alone promotes the primary link. Mapping or dependency changes count as remote
  changes. Intermediate adapter promotion is refused and rolled back. Explicit
  `target=None` withdraws a binding; omission preserves it.
- **Prepare the entire page before singleton application.** The optional
  `prepare_page` hook acquires compound identities and targets in canonical order
  inside the page transaction, before record savepoints. It does database work
  only; all remote facts belong to extraction. Optional `on_revalidated` and
  `on_absent` hooks restore or withdraw native projection visibility in the same
  transaction as the corresponding link status. Absence hooks run only for actual
  status transitions, never repeated observations of an already-unavailable row.
- **A cursor belongs to an epoch.** An invalid/expired cursor or explicit resync
  request creates the next baseline generation. Retain links and revision
  history; reverify links through their generation marker. A stale peer beyond
  tombstone retention requires a baseline. Complete inventory sweeps count
  absences before confirming tombstones and preserve existing quarantine.
- **Inventory is a resumable import.** `reconcile_stream` consumes one bounded
  iterator page per pulse, reading and applying enumerated keys before absence.
  The driver retains its checkpoint in `SyncStream.reconcile_state`; adapters
  seek exclusively after the committed key in their own deterministic ordering.
  Without `supports_identity_reads`, a bounded extraction baseline precedes
  enumeration.
  Callers pulse until the checkpoint is empty; only then is reconciliation
  complete. Root and child absence passes are bounded too.
- **Compound children belong to one aggregate.** A link's optional immutable
  `parent` is a root link in the same stream. Enumerate aggregate keys only:
  children cannot independently become absent, and child retries read and
  reapply the parent's key. The aggregate adapter owns successful child evidence
  and discrepancy resolution; a successful parent alone does not resolve them.
- **Quarantine is not a work queue.** Stream-cycle rescan re-reads due replica
  identities through `BridgeImpl.read_keys` when `supports_identity_reads` is
  declared, including tombstones for missing remote keys. It composes the same
  transactional apply path while preserving the cursor. Only adapters without
  this operation fall back to a baseline, with that fallback recorded in
  discrepancy details. Event feeds skip rescan. Semantic failures back off
  between attempts; conflicts await explicit resolution and never auto-retry.
  The shared driver owns budgets, repeated-page detection and partition
  concurrency.
  Workflow execution and durable scheduling stay with their existing
  owners; integrate must not import workflows.

### Integrations and workers

- **Bridge scheduling follows the final MTI kind.** A child capability may
  extend another bridge (a public Feed extends Channel). The
  [`Integration.concrete_type`](../../addons/angee/integrate/models.py) identity
  selects the one bridge row to schedule and route; querying every ancestor
  would queue the same connection twice. Keep parent-scoped message access on
  the declared Channel relation, with child access inherited through its REBAC
  parent arrow.
- **An integration failure reaches the operator only as an `IntegrationError`.**
  `Bridge.record_sync_error` and the console action results project every other
  exception to the generic "Integration operation failed." — a vendor SDK's
  message may carry tokens, hosts, or request bodies. A backend that wants its
  refusal seen (a rejected IMAP login, an unresolvable host) raises a subclass
  of `angee.integrate.errors.IntegrationError` whose message it composed from
  facts it owns; it never re-raises the vendor exception's text as-is. The
  connection test follows the same rule: `Integration.test_connection` is the
  credential probe, a capability child (a `Channel` → its `ChannelBackend`)
  overrides it with the real handshake, and `test_connection(id)` reports only
  an `IntegrationError`'s `public_message` in band.
- **Native bridge SDKs can abort the interpreter.** Declare process isolation on
  the owning `LiveBridgeImpl`; use the shared integration process host rather
  than serializing selected calls in Python. The session child owns the store
  and account locks until native cleanup completes or the process exits.
  Infrastructure crashes stay retryable; terminal account outcomes retain their
  existing runtime-error latch.
- **Never mix `select()` with buffered `readline()` on a subprocess pipe.** A
  buffered wrapper may consume several complete records while the file descriptor
  becomes non-readable, stranding those records behind the readiness check; it
  can also block mid-line past the stop cadence. Read raw bytes with `os.read()`
  into a manual newline accumulator and drain complete buffered lines before the
  next readiness wait.
- **A long-lived Celery task needs the three-check wake loop.** A session task
  that outlives the tick (a live chat connection) runs on a dedicated queue's
  threads-pool worker — the threads pool enforces **no** time limits, so queue
  isolation is the protection and `time_limit=None` on the task is only
  defense-in-depth. Its loop must wake on a bound (shorter than the reconciler
  tick) and check: (1) the persisted desired-state, so a cooperative stop never
  waits on an idle socket; (2) a process-local `worker_shutting_down` event, or
  a warm SIGTERM wedges behind the pool's blocking join until SIGKILL; (3) that
  it still holds its advisory lock — Postgres advisory locks are
  connection-scoped, and a DB reconnect drops the lock under a live process, so
  the holder must exit for a clean reconciler restart instead of racing a
  duplicate against shared state. The reconciler enqueues with `expires=` of one
  tick so a saturated or absent worker never accumulates a backlog
  (`angee.integrate.session` + `angee.integrate.tasks` are the references).
- **An asyncio vendor SDK owns its loop on the live session's connection thread.**
  Create and run that loop where the vendor connects; the task thread remains the
  only persistence owner. When task-thread work must call an async vendor hook
  (for example, downloading media during ingest), schedule the coroutine with
  `asyncio.run_coroutine_threadsafe` onto that owning loop and wait with a finite
  timeout. Never create a second loop around a coroutine bound to the live client.
- **A lifecycle column is declared intent, never proof of achievement.**
  `integrate.Integration.lifecycle` records what the operator asked for; how far a
  runtime handshake actually got belongs on `runtime_status`/`sync_progress`.
  Overloading one lifecycle value with "not finished yet" — a WhatsApp channel
  that stayed `disconnected` until its worker proved a JID — makes that value
  unusable as a stop signal, because a guard cannot tell "the operator released
  this" from "still connecting". So a worker never writes the lifecycle back: a
  logout or a rejected account is an outcome, not a request, and recording it as
  one reverts the operator within a tick. It reports the failure on the runtime
  axis and stops itself through the desired-state it also owns.
- **An Integration child's second intent axis is the reconciler's to close.**
  A child declaring no `sqid_prefix` of its own *is* an `int_…` row, so the
  generic lifecycle actions (`integrate/schema.py`, `IntegrationActionMutation`)
  move it without the owning addon in the call path. They can only know the one
  axis every Integration has, so any second axis a child adds — a live
  desired-state, a subscription — is unset on rows that arrive that way. Two
  independently writable intent axes need a declared precedence and one owner
  that reconciles the other: let the child's reconciler select on the lifecycle
  and drive its own axis from it, rather than select on its own axis and trust
  something else to have set it. A child's runtime guards must equally honour the
  lifecycle itself — test the one state that runs, not the one state you happen
  to stop on. `angee.integrate.tasks` is the reference:
  `ensure_bridge_sessions` selects CONNECTED and reconciles the live desire to it, and
  gates on `runtime_status` so a known-broken handshake is not redispatched
  forever. Reconcile only when the axes disagree: the write has no dirty check and
  publishes a subscription event, so an unconditional one broadcasts a no-op edit
  per row per tick.
- **A latching gate needs a reset the operator's verb owns.** A reconciler that
  skips rows on `runtime_status=ERROR` disables itself until something clears the
  error — so the repair verb must clear it *itself*, not rely on a lifecycle edge
  doing it as a side effect. `set_lifecycle` returns early when the row already
  reads the target, so a CONNECTED+ERROR row repaired by a verb that only declares
  CONNECTED clears nothing and gets exactly the one dispatch the verb enqueues
  directly; lose that (worker down, queue saturated, a restart) and the row is
  skipped forever. `resume_channel_pairing` reports OK unconditionally for this
  reason. Keep the gate on the shared `runtime_status` rather than a private
  health key: a private one is a further axis the generic verbs cannot clear, so
  it reintroduces the same latch on the generic path.
- **Celery periodic tasks accept `timestamp` when a scheduler supplies one.**
  Static Celery beat ticks call without it, but tests and future scheduler
  backends may inject a Unix timestamp. Keep wrappers tolerant of both shapes.
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
- **Task locks are advisory, row locks are authoritative.** Celery task bodies may
  use `angee.jobs.locks.task_lock()` to prevent duplicate workers from doing the
  same external work, but persisted state transitions still use model/queryset row
  locks, constraints, and idempotent managers. Do not hold row locks during network
  IO.
- **OAuth/OIDC outbound requests must send an honest, non-browser User-Agent.**
  Anthropic's token-endpoint edge 429s spoofed browser/curl User-Agents with a
  `rate_limit_error` (before any auth check) and 403s urllib's `Python-urllib`
  default; an honest client UA passes. `angee.integrate.oauth.client` owns the value
  (`USER_AGENT`); never reintroduce a browser spoof or fall back to urllib's
  default.
- **httpx and httpx2 objects do not mix.** An `httpx2.Client`, which Authlib's
  `OAuth2Client` is from 1.8.0, asserts its own stream type inside the
  transport, so an httpx transport fails on the first real request while
  `MockTransport` tests pass. Integrate's outbound HTTP is httpx2 end to end
  ([`angee.integrate.http`](../../addons/angee/integrate/http.py)). Cover a
  transport or HTTP-library change with a real-socket test such as
  `test_oauth_protocol_round_trips_over_the_real_pinned_transport`.
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
- **Instance names on a row follow daemon-confirmed facts.** Record a daemon
  instance's name when the daemon reports it created and blank it only when the
  daemon confirms it gone. A 409 is the typed
  [`OperatorDaemonConflict`](../../addons/angee/operator/daemon.py) whose `kind` and
  `name` come from the daemon's error body; never parse the message or re-derive
  the name.
- **MCP bearers are per agent and derived from the server credential.**
  `MCPServer.bearer_for()` mints `<agent sqid>.<hmac>` for an internal server; rotating
  the credential (or changing placement) invalidates every provisioned agent's bearer
  until reprovision. The verifier logs each decline with its reason; FastMCP's 401 text
  about "expired" tokens is boilerplate.

### Workflow execution

- **Root subject lifecycle belongs to the record.** Models opt into
  [`RunSubject`](../../addons/angee/workflows/subjects.py) to admit starts and
  retries and settle the first terminal transition. The engine locks the run
  before the subject and keeps these database-only hooks in its transaction;
  UI cancellation, failures and timeouts use the same terminal writer. Bridge
  cycles compose this contract through
  [`SyncCycleBridge`](../../addons/angee/workflows_integrate/sync.py), while
  integrate retains scheduling, queue tokens, advisory locks and dispatch
  compare-and-set. Consumer bridges supply scope locks and an input snapshot;
  they do not create another lifecycle or terminal signal receiver.
- **`Definition` owns graph policy.** The immutable document owns validation,
  class resolution, input bindings, readiness, failure routing and result
  projection. Callers use its methods rather than inspecting node declarations
  to make their own execution decisions. `WorkflowManager` owns identity,
  conditional draft saves and numbered publication. Publication resolves each
  `await_run` child's outcome contract under the author's read scope and freezes
  it in the parent document. Execution reads that frozen contract; a child
  outcome outside it fails the await step, and a changed child contract requires
  affected parents to be republished.
- **A `Step` declares typed input, output and config.** Register its key through
  `ANGEE_WORKFLOW_STEP_CLASSES`. Its `run` method receives a `StepContext` and
  returns a settlement. The context supplies the admitted
  actor, input, checkpoint and attempt identity. Database steps execute inside
  the run's transaction; keep external I/O out of their bodies. IO bodies run
  after the claim commits, outside every transaction. Helpers construct plain
  settlements; each settlement type validates its own values through
  `Step.check` at the body boundary. Retryability, timeouts and stack traces
  belong to the runner's attempt record, not to consumer settlements.
  A failed IO attempt cannot roll back writes already committed by its body.
- **`WorkflowRunQuerySet.hold` owns the run lock and transaction.** It registers
  ready-row dispatch on successful commit. [`Runner.advance`](../../addons/angee/workflows/runner.py)
  requires that lock and composes `Definition` with the persistence owners.
  The runner owns execution and the tick; managers and querysets own row-set
  verbs. It isolates settlement,
  planning and failure recording with savepoints so a later planning failure
  does not discard a successful body write.
- **Queryset verbs own transitions and companion fields.** `StepRunQuerySet`
  owns `claim`, `settle`, `to_waiting`, `to_ready`, `cancel_open`, `dispatch` and
  `count_redispatch`; `StepAttemptQuerySet.close` owns attempt closure.
  Conditional updates check the attempt fence. The runner's periodic tick
  rechecks due waits, expired claims and stale deliveries under `hold`, then delegates to
  those verbs. The effect marker and heartbeat check the same attempt fence
  under the step lock used by the reaper. Uncertain non-idempotent effects wait
  for explicit operator acknowledgement; dispatch exhaustion also waits for an
  operator and never enters domain error routing.
- **Recovery and evidence stay with execution owners.** Operator actions call
  the run and step managers; GraphQL exposes execution resources as read-only.
  Failure preserves unfinished siblings; terminal runs suppress delivery and
  tick recovery. An already-running IO sibling retains its result without
  planning, and retry replans from all retained rows.
  Reprocessing records its predecessor and uses the requesting actor on the
  current publication. Artifacts reference actor-readable records without
  granting access to them. Admission retains the subject and each declared
  record-reference input as shared `DerivedFrom` evidence, after checking the
  run actor's standing read access. Run readers see those references through the
  shared record-reference projection, which redacts target identities when
  access has gone. Paging carries the checkpoint into a fresh claim. A run
  records one protected cause with its origin at start; retention cannot prune
  a cited cause before its effects, and a retained event protects its trigger.
- **Reviews compose one decision per step.**
  [`DecisionStep`](../../addons/angee/workflows/decision_steps.py) asks through
  `ctx.ask(request, state=...)`; `StepRun.decision` retains the question.
  The answer wakes the step after commit, with a tick sweep for missed delivery.
  The resumed body applies the chosen alternatives through record write owners
  as the run actor. A failure rolls the body back, retains the answer and appears
  as an error hold for retry. Non-workflow askers apply synchronously inside the
  verdict transaction; refused application rolls the answer back.
- **Conditional queryset updates send no model signals.** Workflow owners
  explicitly call `publish_change` after their writes. Dispatch locks ready
  rows with `skip_locked` and uses `enqueue_task` to send after commit; callers
  do not infer publication or task delivery from `post_save`.

## Framework Contracts

### Persistence primitives

[Base mixins](../../angee/base/mixins.py) own these row contracts and their
system checks; compose them instead of a local column, comparison, or guard.
Their docstrings own the exact behavior.

- **Optimistic lock:** `OptimisticLockMixin` counts instance saves. A verb that
  accepts a client's `expected_revision` passes it to `save()` and maps
  `StaleRevisionError` to its conflict result; queryset updates never bump.
  Where `revision` is taken (django-reversion's reverse query name on the user
  model), the model renames the counter through `REVISION_FIELD` and its
  GraphQL type still exposes it as `revision`.
- **Creation keys:** `CreationKeyMixin` stores a scoped client creation key and
  content fingerprint. Replays go through `CreationKeyQuerySet.for_creation_key`;
  `angee.E023` requires the declared uniqueness constraint.
- **Ownership:** a grant root composes `OwnerMixin`, so `owner` is transferable
  access and `created_by` remains attribution. Its Zed backs `owner` with that
  column and declares its transfer permission (`angee.E022`); its
  `write__owner` gate names that permission (`angee.E027`). Change it through
  `transfer_ownership`; bulk release belongs only to an owning verb that already
  authorized it. See the [example notes](../../examples/addons/example/notes/permissions.zed);
  remaining `rebac:field=created_by` owner relations are unconverted, not a pattern.
- **Delegated field gates:** a definition that delegates to a parent re-declares
  the parent's field gates through that relation, as enforced for ownership by
  [the base checks](../../angee/base/checks.py). Re-save a fresh, unreloaded
  instance with `update_fields`: without a loaded snapshot, a full save writes
  every column, including `owner`, and must pass every applicable field gate.
- **Owning containers:** a container composing `ItemOwnershipMixin` can own its
  newly inserted items, which then reach access through it; items name it with
  `owner_container` (`angee.E025`) and ask
  [`OwnerMixin.container_owns_items()`](../../angee/base/mixins.py) for its policy.
  Changing the flag affects later inserts only.
- **Write-once fields:** `ImmutableFieldsMixin` rejects changes to declared
  fields; only an authorized owning verb grants the next save an allowance.
- **Archive and trash are different facts.** `ArchiveMixin` soft-hides a row
  from default pickers. `TrashMixin` removes it: who, when and an optional reason
  are stamped and restore re-grants nothing. The model's Zed withholds a trashed
  row from everyone but its managers — a constant `trashed` relation filtered on
  `is_trashed`, subtracted outside any recursive arm (see
  [knowledge pages](../../addons/angee/knowledge/permissions.zed)). The shared
  [`trash_record` / `restore_record`](../../addons/angee/graphql/trash.py) verbs
  check the row's `delete`; metadata marks the flag `trashable` for shared views.
  A domain authority that is not `delete` (record-chatter moderation) keeps its own
  verbs and narrows the shared ones through `TrashQuerySet.trash_targets()`; a
  sudo read path it owns, such as the chatter transcript, must exclude trashed rows
  itself. Work tasks keep their concealing stage as the removal convention.

### Addon WebSocket endpoints

Mount addon sockets through `asgi.websocket_urlpatterns` and compose
`RebacChannelsConsumerMixin` to pin the cookie actor before creating tasks.
The [shared router](../../angee/asgi.py) owns Origin trust and Django session
authentication; never repeat those handshake checks per consumer. Long-lived
protocols that accept writes must revalidate the session at each request and
open fresh evaluator scopes for authorization and change-feed reads.

### GraphQL actor and write contracts

- **View-as is a server-side, read-only HTTP preview.** `X-Angee-View-As`
  carries the target user's public id. [IAM admission](../../addons/angee/iam/models.py)
  checks the real actor's `view_as` permission and the target's eligibility;
  [ViewAs](../../addons/angee/graphql/view_as.py) binds both `request.user` and
  the ambient actor to that target. Mutations and HTTP subscriptions fail with
  `VIEW_AS_READ_ONLY`; query database writes are rolled back. The
  [GraphQL WebSocket consumer](../../addons/angee/graphql/consumers.py) retains its
  handshake actor and does not support this header. [MCP execution](../../addons/angee/mcp/graphql.py)
  carries a synthetic request whose only meaningful attribute is `user`, equal
  to the ambient actor's eligible user (anonymous for a non-user or inactive
  user); it has no HTTP headers or view-as preview lifecycle.
- **Concurrency and replay tokens are GraphQL root arguments.** On models
  composing `OptimisticLockMixin`, `update_<resource>_by_pk` accepts
  `expected_revision: Int`; a stale value fails with `STALE_REVISION`. On models
  composing `CreationKeyMixin`, `insert_<resource>_one` accepts
  `client_creation_key: String`; replaying the same scoped key and content
  returns the readable original, and changed content fails with
  `CREATION_KEY_CONFLICT`. Neither token belongs in `_set` or `object`.
  The [Hasura adapter](../../addons/angee/graphql/data/hasura.py) declares and
  consumes both arguments through the [base owners](../../angee/base/mixins.py).
- **Permission answers reuse the authorization owner.** Declare a type's
  `permissions` through [`permissions_field(names)`](../../addons/angee/graphql/capabilities.py);
  it reports only the declared Zed permissions held by the current actor,
  validates names at schema build and batches list evaluation in SQL.
  Server predicates compose the same owner's `held_permissions(record, names)`.
  View-as therefore reports the target's permissions. Clients consume these
  answers; they never reconstruct authority from roles or identity.

### Direct record access

[`RecordRefMixin`](../../angee/base/refs.py) owns the canonical content-type
and public-ID projection for a generic record pointer.
[`RecordReferenceNode`](../../addons/angee/graphql/relations.py) and
`with_record_reference_access` project it into GraphQL under the current
reader's scope. A hidden target yields null identity fields; its model and ID
must also stay unavailable through resource filters and aggregate counts. Addon
resources declare their record-reference filter axes through the shared Hasura
resource owner instead of reimplementing this projection.

Models opt into direct sharing with `rebac_grantable`, mapping each relation to
the permission required to manage it. The model remains the policy owner:
`direct_record_access(relations=...)` lists only the caller-authorized declared
subset, and `validate_record_access_target()` enforces model-specific target
rules for listing, options, grants, and revocations. For example, Workflow
accepts grants only on its lineage head; GraphQL must not infer that rule from
Workflow fields. A `validate_record_access_subject()` override that keeps one
user out of a record's holders raises `RecordAccessSubjectRefused` when
[`subject_reaches_user`](../../angee/base/actors.py), the one predicate for
whether a subject reference resolves to, or contains, that user, answers true;
it never decodes wildcards or usersets itself.

The public GraphQL recipient is typed as either a user or group. It resolves to
the canonical REBAC subject (`auth/user:<id>` or
`auth/group:<id>#member`); arbitrary subject strings are not accepted. Historic
unsupported, wildcard, or malformed subjects remain visible as raw audit
subjects with a null typed recipient.

Use `authorized_action_target` for mutation preflight that requires the native
unredacted write scope. A surface that manages a declared relation under another
permission uses `authorized_permission_target`, which resolves through that
exact actor permission while preserving scoped not-found behavior and the final
row access check.

Framework contracts should be self-explaining in code. Add docstrings to public
modules, classes, methods, functions, declarative manifest attributes, and public
module-level constants. Add docstrings to private helpers when their role is not
obvious from the function name and signature. Do not maintain a parallel spec, field inventory, or model
API list for behavior that can live clearly beside the code.

The addon's `addon.toml` owns declaration facts, parsed into hatch-angee's native
manifest. AppConfig owns Django's identity, registration and lifecycle. Each
capability owner interprets its own declarations and conventional defaults; it
never creates a separately configurable manifest mirror. Model/manager behavior
stays with the native class, and composition lifecycle stays with the compiler.
Keep the exact authoring forms in owner docstrings and the upstream manifest
contract, rather than duplicating them in this guideline.

Before decomposing backend code, classify each fact by its Django owner:

- Persisted choices live beside the model field, usually as model-owned
  `TextChoices`.
- Row-set behavior lives on managers and querysets.
- Instance behavior lives on model methods and properties.
- Addon declarations live in `addon.toml`; capability owners resolve their paths from Django `AppConfig.path`.
- Management commands parse arguments and dispatch to the owning model, manager,
  service, or composer function.
- Compatibility facades exist only for an explicit compatibility promise.

The project settings contract declares project facts; Angee owns Django
composition wiring. Anchor project defaults to `BASE_DIR`, never the current
working directory, and use Python settings only for facts that need Python.
Follow [settings bootstrap](../composer.md#settings-bootstrap) and
[autoconfig](../composer.md#autoconfig) for the current loading and merge owners.
`Composer` resolves settings and app order; `Runtime.configure_migration_modules`
binds generated labels during Django phase 2. Keep YAML loading bounded to the
project's settings and explicit configuration file: an enclosing stack must not
silently contribute settings to a nested project.

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

Use [the verification matrix](../checks.md) for commands, execution roots, and
required checks. Run focused tests while editing, then the applicable broad
checks before handoff; report any unavailable check and its actual blocker.

Before adding a backend abstraction, search for the native owner first:
`rg "AppConfig|schemas|permissions|resources|autoconfig"`,
`rg "QuerySet|Manager.from_queryset"`, and
`rg "apps.get_model|get_app_configs"`. If the change introduces or extends a
seam, add a focused guard in the owning test area: layering in
[`tests/test_base_layering.py`](../../tests/test_base_layering.py) (dependency
direction) and [`tests/test_layering.py`](../../tests/test_layering.py) (import
closures), addon/manifest binding in app tests, settings/autoconfig/app graph
behavior in [`tests/test_settings.py`](../../tests/test_settings.py), runtime
emission in [`tests/test_compose.py`](../../tests/test_compose.py), migration
history in [`tests/test_runtime_migrations.py`](../../tests/test_runtime_migrations.py),
and schema composition in GraphQL tests. Preserve the focused proof obligations
in the relevant pitfalls: isolated test-module runs, non-admin authorization
coverage, narrow GraphQL selections at multiple row counts, and populated-state
data-migration checks.

### Verb eligibility projections

Record controls consume eligibility from the verb owner. Task audience domains
extend [`Task.visibility_blockers`](../../addons/angee/projects/models.py): each
SQL condition names its validation exception. The locked visibility verb and
optimized `allowed_visibility` projection share those conditions and native
permission scopes. Hidden domain facts stay inside the projection query.
Message writers must still preserve publication invariants under the task lock.

Decision admission, answers and withdrawal belong to the
[decisions manager](../../addons/angee/decisions/managers.py). Asking owners
consume its verdict; controls consume the owning verb's eligibility.
