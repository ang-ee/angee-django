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

When extending a generated manager, bind native `Manager.from_queryset(...)`
to a module-level name before using it as a class base, as in
[`AngeeManager`](../../addons/angee/base/managers.py). Leave that assignment inferred:
direct class-base calls and `Any` annotations bypass django-stubs' native factory hook.

## Django-Native Rule

Angee supplies reusable Django addons and their capability integrations.
`django-compose` owns generic app selection and build-time model composition;
Django owns the resulting application lifecycle and ORM.

Before adding an Angee abstraction, ask: does Django already have an object,
method, or convention that owns this fact?

Use Django's native owners:

- Django app identity and lifecycle live on `AppConfig`; addon declarations live
  in `pyproject.toml` and are validated against that identity.
- Model behavior lives on models, managers, and querysets.
- Value coercion lives on fields.
- Command dispatch lives in Django management commands and `argparse`.
- Table names, app labels, migrations, and model metadata follow Django
  defaults.

Angee addons own capability semantics: merging GraphQL schemas, projecting
permission policy, syncing declared resources and contributing web fragments.
Generic discovery, dependency ordering and generated model publication belong
to `django-compose`; callers consume those results instead of rebuilding them.

A wrapper must prove it adds a real new concept. If it only forwards,
normalizes, or renames a Django object, delete it.

Django-native also means app-native. Addons are reusable Django apps with
conventional files: `pyproject.toml` declares package metadata, and its
`angee.addons` entry point identifies the package as an addon. A pyproject alone
may describe an ordinary library. `models.py` owns data and row behavior,
`managers.py` owns reusable row-set APIs when they outgrow the model module,
`schema.py` owns Strawberry declarations, `permissions.zed` owns REBAC structure,
`mcp_tools.py` owns MCP tool registration, `forms.py` owns Django form
validation/presentation, `admin.py` owns Django admin presentation, and
`management/commands/` owns CLI parsing. `apps.py` is optional; use it to customize
native Django config or lifecycle hooks such as `ready()` / `import_models()`.
An addon without that customization uses Django's auto-created config. Keep
requirements in package metadata and capability facts in their tool tables even when an explicit config exists. A proven
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

Generic Django composition belongs to the `django-compose` dependency. It owns
no Angee/product vocabulary and does not import framework addons. Angee has no
separate core wheel or namespace initializer.

- `angee.base` owns reusable models, fields, managers, settings/deployment glue,
  package-data projection and small shared primitives. It consumes declared
  native Django, compiler and permission-library contracts.
- `angee.server` owns optional HTTP defaults, Uvicorn commands and connection
  policy. Web, GraphQL and MCP require it through native package requirements;
  model-only projects do not activate a serving stack.
- Capability addons own their own policy and package facts. Cross-addon imports
  follow declared native requirements toward stable owners.
- Source models may inherit `django_compose.models.Composable`. Settings,
  serving and command callers use the compiler's public runtime seams. Serving
  code never imports compiler build modules or an addon's `build.py`.
- Explicit capability integrations implement structural protocols in
  `django_compose.declarations`; dotted `COMPOSE_INTEGRATIONS` entries defer
  imports until build phase 2. The compiler owns publication and verification.

### Composition owners

Use these owners instead of maintaining another contract in an addon:

| Concern | Owner and durable rule |
|---|---|
| Addon discovery and declarations | The native `django_compose.components` index owns identity/closure; [`angee.base.addons`](../../addons/angee/base/addons.py) reads capability-owned package TOML. Addon requirements stay in `[project].dependencies`; foreign app edges use django-compose's `AppConfig.requires_apps` extension. |
| Settings and app ordering | [Settings bootstrap and app graph](../composer.md#settings-bootstrap) compose one app set. Keep source-model imports out of settings loading; do not create separate build/run registries. |
| Capability conventions | [Addon declarations](../composer.md#addon-declarations) identifies the readers. Each capability reads its own `[tool.angee]` table and conventional defaults; dependencies, resource tiers, and intent stay explicit. |
| Models and runtime import | [Runtime build and import](../composer.md#runtime-build-and-import) describes source selection and Django phase 2. Use narrow abstract contributors; let Django own inheritance, field cloning, managers, and registration. |
| Migration history and cleanup | [Addon-owned runtime migrations](../composer.md#addon-owned-runtime-migrations) and [migration policy](#migration-history) distinguish replaceable generated models from version-controlled native Django history. |
| Resource load hooks | [`ResourceLoadMixin`](../../addons/angee/resources/mixins.py) is the resources-owned terminal mixin. Contributors delegate exactly once through `super()`, even when skipping local work; an exception aborts the transaction, so never delegate in `finally`. A row whose state only an owner verb may write declares a `resource_class` adapter that persists through that verb as the row's named actor and records the result in the ledger ([workflow definitions](../../addons/angee/workflows/resources.py), [chatter](../../addons/angee/messaging/resources.py)). |

- **Compose addon discovery through its native owners.** Follow the
  [discovery flow](../composer.md#addon-discovery) for available candidates,
  Django identity, and persisted catalogue state. Serving code never imports
  compiler build code just to list addons.
- **The resource ledger is owned by the resource addon.** The composer discovers
  `angee.resources.models.Resource` as a normal addon source model and emits it
  under the `resources` label. The generic compiler never imports the resource addon.
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
- **Database routing:** a persisted row's database is `row._state.db`; bind a
  manager for an unsaved or row-relative operation with
  `Model._base_manager.db_manager(hints={"instance": row})` and use that
  manager's `db`. Do not thread database aliases through Angee instance methods
  or hooks. Use native relation access, `refresh_from_db` when deferred or stale
  fields need reloading, and ordinary `atomic` and `on_commit` boundaries while
  preserving locks and batching. A declared
  read-only external import source may select its database explicitly.
  Multi-database support extends Django with a router for static model placement;
  add operation scoping only for a concrete need. REBAC-managed models and their
  relationship store must share a database alias. The upstream
  `rebac.checks.check_policy_models` check owns that routing rule; run native
  checks for the aliases selected by the deployment rather than imposing a
  second framework rule that requires the literal `default` alias.
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
  Build computed filter expressions only for aliases referenced by the resolved
  native predicate, shared by requests and stored conditions. Preparing an unused
  expression still costs Python work even when Django omits it from SQL.
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
  belongs to `django_compose.conf`; model materialization belongs to the native
  compiler build owner.
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
  an import path.
- Source models are abstract. Concrete apps are emitted by the composer.
- Keep Django `Meta` for Django and library-owned options such as
  `rebac_resource_type`; Angee extension facts live on the owning model class.
- Foundation and capability addons own model markers, decorators and field
  projection facts. Their declared build integrations contribute literal
  declarations; `django-compose` emits them without knowing the owning policy. If runtime code consumes a
  source-model structural marker, the composer carries it onto the emitted
  concrete class instead of callers inheriting or probing the abstract source.
- Field classes own data-resource classification declarations. Field authors set
  `angee_widget` and `angee_currency_field` on the field; native Django and
  Strawberry field types own scalar classification.
  `angee.graphql.data.resource_fields` reads those declarations and does
  not special-case addon-owned field classes. Custom widget keys follow the
  [frontend registry naming rule](../frontend/guidelines.md#rules); the owning
  web addon registers the identical key. Unknown bare built-in names remain
  schema errors.
- A computed GraphQL field may declare presentation facts through
  `strawberry_django.field(metadata=...)`. The shared classifiers in
  `angee.graphql.data.resource_fields` own resolution: surface metadata first,
  Django field declarations second, then the ordinary type fallback. Money
  projections use `MONEY_CURRENCY_FIELD_METADATA_KEY` from its owner
  `angee.graphql.data.resource_fields` instead of repeating its metadata key. Presentation metadata alone grants no
  ORM write or aggregation capability; those still come from a real model field
  or explicit resource input policy.
- Manually ordered rows use `FractionalRankField` (NOT NULL) plus a database
  `UniqueConstraint` over their context fields and rank. Use the field's
  append allocation when a rank is omitted. `FractionalRankExhausted` refuses
  the write; an ordering owner must perform any deliberate contextual reranking
  under its own lock. For cyclic reorders, the field's `get_temporary_ranks`
  allocates outside the stored context and requested destinations; the owning
  writer stages and finalizes through its native queryset/model lifecycle in
  one transaction. Both phases retain capture and a failure rolls back the whole
  reorder. Never guess an epsilon or reuse a rank. Task board drops use the
  projects-owned `task_order` input extension: visible neighbor ids express
  intent, while Task resolves its persisted project scope (including no project)
  and the field allocates between adjacent stored ranks. Assignee/stage lanes
  never define uniqueness. Task's ordering transaction covers ordinary explicit
  rank writers and creation too; the Hasura adapter acquires it before its row
  lock. Explicit numeric ranks, including Stage positions, retain exact values.
- Runtime model modules, generated schemas and codegen stubs are output.
  Change their source declarations. Native Django migration history is reviewed,
  version-controlled project source; preserve it across runtime rebuilds.
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
    `angee.base.fields.ImplClassField`** naming a non-model
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
  Do not add blank-string sentinels or per-field GraphQL coercion;
  [`StateField.check()`](../../addons/angee/base/fields/__init__.py) rejects that declaration on
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
  setting named by `Base.registry_setting`; contributors add paths through an autoconfig
  dotted key (`"ANGEE_…_CLASSES.<key>": "<dotted.path>"`). So a writable column
  never feeds `import_string` (the path comes from composed, trusted settings,
  like an addon's `schemas` reference), the available impls are a composition
  fact rather than a base-model import, a project can remap a key to its own
  class, and its native field check validates every mapping entry's path, base
  class and key. Owning addons register native checks for rowless contracts. Because every addon has contributed by schema-build
  time the key set is closed, so the field is a `TextChoicesField` and
  `strawberry-django` renders the GraphQL enum natively (like `StateField`). It
  therefore requires a **non-empty** registry: an addon whose impl set could
  otherwise be empty registers a noop/null-object default (storage's `local`;
  integrate's `none` VCS client), so a composition always has one selectable
  impl and the enum is never empty.
- **Rowless implementation catalogues compose the same registry owner.**
  [`angee.base.implementations`](../../addons/angee/base/implementations.py) owns registry checks, choice
  metadata for fields and settings-selected uses. Native enum projection belongs
  to [`ImplClassField`](../../addons/angee/base/fields/implementation.py).
  Callers name only the base (`resolve_impl_class(Base, key)`, `impl_choices(Base)`).
  Empty catalogues may pass checks and return no choices; projecting an enum
  requires entries. The owning addon registers a stable native Django check using
  `check_impl_registry(Base, **kwargs)` for a rowless contract; no second declaration
  list is required. Keep any selected-key policy with its addon. GraphQL adapters
  live in [`angee.graphql.impl`](../../addons/angee/graphql/impl.py); callers
  retain authorization policy. Models with picker restrictions extend the existing
  `ImplementationChoicesMixin` policy; do not put product filtering on a field subclass.
- **Typed row implementation config has one owner.** A model composing
  [`ImplDefaultsMixin`](../../addons/angee/base/models/implementations.py) uses one
  effective implementation selector for its concrete `config` JSONField. Native
  model checks reject a missing/wrong config field or ambiguous typed selectors.
  Untyped field defaults and rowless implementation config do not require a row
  config column. Pydantic remains the selected implementation's validation owner.
- **An `ImplClassField` builds its enum at model-import time from its base's
  `registry_setting`** — the key→path mapping (e.g. `ANGEE_STORAGE_BACKEND_CLASSES`)
  is supplied by the owning addon's `autoconfig`, so every settings module that
  installs the addon must carry a **non-empty** mapping. Bare settings modules
  declare the required registry inputs explicitly before
  Django loads models, then add only their fixture implementations. An empty
  registry raises `ImproperlyConfigured` at import — give the addon a
  noop/null-object default so the set is never empty. The column stores the key
  (`local`), never a dotted path. A deconstructed migration-state field omits
  `base_class` and may reconstruct its declared default after the registry has
  been removed; active model fields still require a typed base and a non-empty
  registry.
- **Pydantic declarations own implementation config defaults.**
  [`ImplBase.config_defaults()`](../../addons/angee/base/implementations.py) reads static input
  suggestions from Pydantic's validation JSON Schema independently of FormSpec
  support; [`angee.base.schemas.forms`](../../addons/angee/base/schemas/forms.py) projects the
  same declaration for transport-neutral forms. Dynamic factories
  resolve through `parse_config()` at runtime; `normalize_config()` serializes
  that parsed model with its wire aliases. Typed implementation values share
  [`get_type_adapter`](../../addons/angee/base/validators.py)'s cached native adapter
  and Django field-path errors. Explicit
  config parsing rejects non-empty values without a model; model-row validation
  still leaves undeclared legacy config alone. Generated choice metadata stays
  deterministic.
- **Hooks are declared callable settings, separate from keyed registries.**
  The owning addon registers native Django checks for its specific callable
  settings. [`angee.base.hooks`](../../addons/angee/base/hooks.py) supplies shared import and
  validation with `check_hooks`, `resolve_hook` and ordered `resolve_hooks`.
  Checks import providers without executing them. There is no second list of
  hook-setting names; each capability owns its validation and invocation.
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
  The native enum and choice projections belong to `angee.base.implementations`; callers
  without a model column use that owner after settings composition.
- **Extension is symmetric across five axes — extend, never edit the owner — and
  the schema is built after the runtime is composed, so all five apply
  post-composition with the dependency staying one-way (downstream reaches up; the
  upstream never references down).** Add a *concrete subtype* of a parent row with
  a Django child model when exactly one concrete kind applies; add a *field* to
  another addon's model with an abstract `Composable` source declared with `extends="app.Model"` when the same
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
  [`InputExtensionsMixin.apply_input_extensions`](../../addons/angee/base/models/inputs.py),
  composed by `AngeeModel`, with nested line rows included. Input-owning mixins
  also compose this fieldless terminal when they support plain Django hosts.
  Its consumer (a mixin or `extends` donor on the model) takes
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
- **Derived columns have two drift classes and two owners.** Signals own instance
  saves/deletes, cascades, and queryset deletes; idempotent repair passes own
  `bulk_create` and queryset `update` paths, where signals do not run.
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
- **JSON Schema concerns have explicit owners.**
  [`angee.base.validators`](../../addons/angee/base/validators.py) validates declarations
  and values through the native JSON Schema libraries and translates Django errors.
  [`base.schemas.references`](../../addons/angee/base/schemas/references.py) owns
  bounded local-reference operations. Workflow-specific schema composition stays
  in [`workflows.schemas`](../../addons/angee/workflows/schemas.py).
  [`FormSpec`](../../addons/angee/base/schemas/form_spec.schema.json) is the one
  contract for Python projection, generated TypeScript types and UI validation;
  messaging composes it with its own public-form admission policy.
- **Actor-scoped scalar subqueries and keyset cursors belong to `angee.base`.**
  The `Coalesce(Subquery(related.with_actor().scoped().filter(pk=OuterRef).values(v)[:1]), "")`
  shape and the signed `(order_at, pk)` cursor pager are framework primitives;
  do not copy them into another addon's queryset.

## REBAC

REBAC is owned by `django-zed-rebac` (see the Rules entry and `docs/stack.md`).
This project runs **fail-closed**: `REBAC_STRICT_MODE=True` and
`REBAC_SUPERUSER_BYPASS=False`, so every actor — superusers included — reaches
data through REBAC, never a queryset bypass.

- **The local backend is the only supported REBAC backend.** Base
  [autoconfig](../../addons/angee/base/autoconfig.py) selects it because field- and
  const-backed relations, `authenticated`, and SQL read scopes are evaluated
  against the Django rows themselves, which no remote tuple store can see. Do not
  mirror column facts into tuples to keep another backend viable.
- **Permission fragments widen `read_granted`; the owner derives `read`.**
  Consumer addons union their additional read arms into the resource owner's
  `read_granted` permission. The owning definition alone derives `read`, so its
  withholding and other final policy cannot be bypassed or copied by fragments.
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
  [the public identity boundary](../../addons/angee/base/identity.py) before reaching
  REBAC, and outputs encode the PK there. Signed tokens are transport too: a
  download token carries its issuing actor's public subject, and
  [`FileManager.for_download_token`](../../addons/angee/storage/models.py)
  re-checks that actor's `read` on every request. Tableless role anchors keep
  named IDs. Changing a public prefix or codec must never change grants.
- **Container inheritance belongs to the resource and scope owners.** A
  resource's FK relations and arrows live in its own Zed definition. A scope
  contributes additional relations and arrows through its own
  `permissions.extends.zed`. Binding a resource widens access to its contents,
  so the binding edge's own `create` and `delete` require authority over both
  ends: [project bindings](../../addons/angee/projects/permissions.zed) take
  share on the project and the resource type's grant authority through the
  relation declared for that type, and
  [`projects.access.bind`](../../addons/angee/projects/access.py) writes them
  under the actor.
- **Declare direct sharing once.** Models declare `rebac_grantable`; the
  [record-access API](../../addons/angee/graphql/sharing.py) dispatches bulk
  grants and revocations through the model's checked methods. Addons do not
  define private share mutations. Metadata projects the grant surface and the
  subject resource's public identity field; the API converts selected public
  subjects to canonical PK references before validating and writing grants.
- **A record authorizes its tags.** A taggable model's addon depends on
  `angee.tags`, composes [`TaggedModel`](../../addons/angee/tags/models.py) on
  the canonical model (its reverse generic relation is a private field: no
  column, no migration, and the delete collector cascades the edges), declares
  the type's `target`-backed relation on `tags/tag_assignment` from its
  `permissions.extends.zed`, composes
  [`TaggedNode`](../../addons/angee/tags/schema.py) onto the console node — or a
  console `type_extensions` donor when the node is shared with the public
  schema — with `tags_input_extensions` on its console inputs, and places the
  field in its web fragment. The native prefetch hint batches every row's
  readable edges and tags once per page under the actor's scope. Adding or
  removing a tag, through the record's save or the `tag`/`untag` verbs, needs
  write on the record; tag administrators curate only the vocabulary. An edge is
  read by whoever reads both the tag and the record. `TagAssignment.check`
  fails startup when the taggable models and the declared relations differ. The
  tags addon names no other addon's model.
- **Raise through the model's access owner.** `require_access(permission, actor=None)`
  delegates to native REBAC checks. An explicit actor stays bound to the instance;
  omitting it preserves native instance and ambient scope precedence. Verbs resolve
  attribution separately when they need to record a requesting actor.
- **Names for everyone; the directory for people managers.** Every signed-in
  subject reads an account's name (`auth/user#read` is `authenticated`), so
  records embed people freely. The sign-in name, email, last sign-in, staff and
  active state and preferences are field gates for the person and people
  managers (`read_private`). The full list follows `auth/user#list`: platform
  admins, named directory readers and the roles a consumer unions in. Pickers
  follow `auth/user#colleague`: the person, people managers' full directory,
  and the identity arms owners contribute from their fragments. An arm is a
  relation on `auth/user`, so its backing rows' writers need `write` on the
  account; keep it to rows only identity flows write.
  Human-only membership pickers use the user collection's people. IAM owns the group model and declares its native
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
- **Login throttling belongs at the IAM auth seam.** Do not add per-view or
  per-test throttles; IAM composes `django-axes` at the `authenticate(request=...)`
  backend/signal path, so the password GraphQL mutation stays a thin caller.
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
- **Visibility and access are REBAC-native, always.** Put relations and
  permission arms on the model's zed and let the store scope reads; never stand
  authorization up with a Python provider, `visible_to` projection, or queryset
  filter. Scope roles live on the scope definition, and scoped models derive
  arms from them (`scope->viewer` for read, `scope->editor` for write).
- A `read__<field>`-gated field is never filterable, sortable, groupable, or aggregatable.
  Filter or order on its gate's terms instead: a named filter expression or
  queryset alias built with REBAC's `queryset.readable(field)` is NULL wherever
  the actor lacks the gate, as IAM's `active` filter is. A rule that must filter on a gated column for everyone (pickers offer
  only active people) is a deliberate disclosure: declare it in Zed with a
  filtered constant on the model's own column and a permission that names it,
  then match by primary key against that permission's rows, as IAM's
  `active_account` and `UserQuerySet.active_people` do. No bypass, no audit row.
  Uniqueness and constraint validation compare a row with every row, so
  native `RebacMixin` runs them under a named system context
  once the actor holds the write's access (`create` on the unsaved candidate,
  `write` on a saved row). A field gate is not creation policy: the library enforces `write__<field>` on
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
- **Posture is data, not schema.** `permissions.extends.zed` declares addon-owned
  capability permissions and unions existing permission arms; deployment-specific
  narrowable defaults still ship as seeded tuples. Platform-wide tuple-driven visibility
  uses a const-backed singleton relation on each row (for example,
  `auth/user#directory` → `iam/directory:main`) plus grants on that singleton
  (`iam/directory:main#reader` names people managers), never base schema arms or
  per-row fan-out a deployment cannot omit.
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
  [`angee.E024`](../../addons/angee/base/checks.py) rejects caveated subjects in every
  effective schema. Actor-scoped querysets carry no caveat context. Express
  row-dependent conditions as live field-backed relations.
- User-requested reads and writes retain their actor scope. Reserve
  `system_context`/`asystem_context` for named system-owned work; do not elevate
  a user factory merely because it inserts a row. A gated factory that must
  elevate to create a row returns that row bound to the requesting actor, so
  callers continue under the original actor, never the elevated one.
- **Elevated destructive work keeps its dependents' authorization.** A verb that
  checks the public `delete` permission itself and then runs the owned
  destructive work under `system_context` (storage's soft delete, messaging's
  threaded-record delete) never hides independently-authorized
  `on_delete=CASCADE` children under that elevated cascade; a dependent row
  derives `delete` through its parent in its own zed relation.
- **Merge re-points references as named system work.** This is a deliberate
  exception to the actor-scope rule above.
  - [`MergeableMixin.merge`](../../addons/angee/base/models/merge.py) first authorizes the actor:
    it finds the merged records through the actor's read scope, even inside an
    outer system context, and needs `write` on the survivor and `delete` on each
    merged record.
  - Only then does it call the system fold, [`MergeableMixin._fold`](../../addons/angee/base/models/merge.py),
    which moves references under `system_context(reason="merge")`, including
    deleting a merged record's copy of an identity row the survivor already
    holds. Under an actor, REBAC only deletes and recreates an edge, which would
    cost attachments their identity and dependants.
  - Retiring a merged record (`retire_merged`) keeps it as a redirect
    (`merged_into`, plus trash on a trash owner) where `MergeableMixin.retains`
    says so: the model retains every merged record, it composes `TrashMixin`, or
    live or historical references name it. Only an unretained record is the
    actor's own delete. Readers resolve a redirect through `canonical()`; a
    `merged_from` row stays visible as such.
  - A model's own system verb folding a duplicate (no actor to authorize) calls
    the system entry [`MergeableMixin.fold`](../../addons/angee/base/models/merge.py),
    which joins the open changeset and runs the same hooks under a system
    boundary. Before core row locks, both entries call `lock_for_merge()`
    so a domain can acquire its earlier lock class. Querysets use
    `redirect_group(survivor)` for the survivor plus its flattened one-hop
    redirects.
- Native REBAC `create`/`insert` evaluates the unsaved candidate's field- and
  const-backed relationships, so per-row `create` gates remain authoritative.
  Compose that path for ordinary factories, as
  [`AgentSessionManager.start`](../../addons/angee/agents/models.py) does.
  Manual factories needing an explicit relationship preflight call native
  `rebac.check_new`; prepared rows use the manager's native `insert` path and
  retain the verified actor on the instance.
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
  owned by `angee.base.build`. The `tests/extcontrib` fixture keeps the
  mechanical merge covered. Each
  `definition <target> { … }` block in the fragment names an existing definition
  and lists the relations it contributes and the permission arms it unions in
  (`permission read = <term>` merges to `read = (<base>) + (<term>)`). The
  composer merges every fragment into its target's owning package at build time,
  emits the merged effective zed to `runtime/permissions/<package>.zed`, and
  repoints that package's `AppConfig.rebac_schema` at it, so `rebac sync` /
  `rebac check` both read the additive superset. The merge fails fast on a relation-name collision (base or two
  contributors), a new permission name introduced by two contributors or
  colliding with a relation, and a target no installed package declares;
  contributors merge in sorted package order. New capability permission names
  belong to one introducing addon; references pass native schema validation.
  A new field gate, `read__<field>` or `write__<field>`, additionally requires a
  concrete column that the same package's donor contributes to the target model;
  no other package may extend that declaration. [`angee.base.build.field_gate_owners`](../../addons/angee/base/build.py)
  interprets the compiler's neutral donor-field provenance for REBAC.
  Functional drift is caught by `rebac sync` (content hash) and `angee check`
  (the emitted file); the contribution is revisioned by the contributing
  addon (`@rebac_schema_revision` in its fragment, echoed into the merged file's
  `@rebac_extended_by`), so the extended addon does **not** bump its revision for an
  additive extension. **Editing a framework addon's `permissions.zed` to name
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
  `angee` autoconfig. Since
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
  the sync. Unstick with `rebac --skip-checks sync --prune-stale --yes`, then
  `check`; never smoke-test a zed against the shared example DB.
- The local backend compiles permissions into queries over application tables;
  it maintains no permission index. Run `migrate` and `rebac sync` to prepare
  the database and store the schema. There is nothing to rebuild or verify.
- `REBAC_DEPTH_LIMIT` bounds compiled SQL nesting and inherited permission hops.
  On SQLite (default 8), access inherited through more than eight parent hops is
  denied: scoped lists omit those rows; point checks raise `PermissionDepthExceeded`.
  Deeper hierarchies need PostgreSQL (default 16). [Base autoconfig](../../addons/angee/base/autoconfig.py)
  owns the defaults; explicit environment values take precedence over project settings.
- Native `rebac --skip-checks sync --prune-stale --yes` retires removed
  package-managed declarations and their stored relationships in the same policy
  transaction as synchronization. Provision runs it after migrations preserve
  identity changes and before system checks. This includes removed addons and
  packages whose last `permissions.zed` disappeared. `--force-overwrite` remains
  a separate explicit choice for replacing locally managed live policy.

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

The environment, test-registry and running-stack pitfalls that lived here now
belong to the `angee-tests` skill: [environment pitfalls](../../.agents/skills/angee-tests/references/environment.md).
The development server contract stayed under [Development server](#development-server)
and the module-naming rule under [Naming](#naming).

### Migrations and runtime

The migration and runtime procedures that lived here now belong to the
`angee-runtime` skill: [migration pitfalls](../../.agents/skills/angee-runtime/references/migrations.md).
The migration policy stayed under [Migration history](#migration-history) and the
history contracts under [Persistence primitives](#persistence-primitives).

### Models, queries, and resources

The query, model and seed pitfalls that lived here now belong to the `angee-models`
and `angee-resources` skills: [model pitfalls](../../.agents/skills/angee-models/references/pitfalls.md),
[resource pitfalls](../../.agents/skills/angee-resources/references/pitfalls.md). The contracts stayed
in this guide: model rules under [Rules](#rules), row primitives under
[Persistence primitives](#persistence-primitives), generic edges under
[Direct record access](#direct-record-access), the loader under [Resource loading](#resource-loading),
timeline reads under [Schema lifecycle and change publication](#schema-lifecycle-and-change-publication).

### GraphQL and authorization

The GraphQL input and authorization pitfalls that lived here now belong to the
`angee-graphql` and `angee-permissions` skills: [GraphQL pitfalls](../../.agents/skills/angee-graphql/references/pitfalls.md),
[authorization pitfalls](../../.agents/skills/angee-permissions/references/pitfalls.md). The contracts
stayed under [GraphQL actor and write contracts](#graphql-actor-and-write-contracts),
[Schema lifecycle and change publication](#schema-lifecycle-and-change-publication) and [REBAC](#rebac).

### Record sync

The record-sync protocol that lived here is a contract, not a pitfall: it moved
under [Integration and worker contracts](#integration-and-worker-contracts). The
integrate migration procedures belong to the `integrate-bridges` skill:
[integrate pitfalls](../../addons/angee/integrate/skills/integrate-bridges/references/pitfalls.md).

### Integrations and workers

The bridge, live-session and outbound-HTTP pitfalls that lived here now belong to
the `integrate-bridges` skill: [integrate pitfalls](../../addons/angee/integrate/skills/integrate-bridges/references/pitfalls.md);
the Celery task pitfalls to the `jobs-tasks` skill: [task pitfalls](../../addons/angee/jobs/skills/jobs-tasks/references/pitfalls.md).
The contracts stayed under [Integration and worker contracts](#integration-and-worker-contracts).

### Workflow execution

The execution contracts that lived here moved under [Workflow contracts](#workflow-contracts).
The signal pitfall belongs to the `workflow-definitions` skill:
[workflow pitfalls](../../addons/angee/workflows/skills/workflow-definitions/references/pitfalls.md).

## Framework Contracts

### Persistence primitives

[Base models](../../addons/angee/base/models/) own these row contracts and their
system checks; compose them instead of a local column, comparison, or guard.
Their docstrings own the exact behavior.

[`StatementRecords`](../../addons/angee/provenance/statements.py) composes simple-history's field
copying into typed statement models. Their [manager](../../addons/angee/provenance/statements.py)
owns locked assertion writes and survivorship; the canonical subject owns source
eligibility and application. Like history tables, statements have no independent
REBAC resource or ORM read policy. Read them through their subject: the
subject-bound `instance.statements` accessor once the subject row is in hand, or
`StatementQuerySet.for_reader(actor)` for a cross-record surface such as Review,
which scopes rows to the subjects the actor may read. A statement read never
widens what the subject reader may see: related rows (the member, the asserting
principal) resolve through their own read gates at the serving seam, no group or
filter axis reads a related row's columns, and serving surfaces own gated-field
redaction. Writes use the asserting source's native REBAC write scope on the
subject. Merge collisions remain retained as superseded rows outside the current
per-aspect key. `Statement.sort_key(aspect)` is the one trust ordering — preferred
rank, the aspect's source precedence (`Aspect.priority`), confidence, the row
class's `tie_break()`, the stance's update time (bookkeeping — `applied`,
supersession, a merge move — keeps it), primary key — shared by survivorship,
cross-subject member resolution (`Handle.objects.resolve`) and merge collapse;
`trust_ordering` is the SQL display order of the same stances. The model owns
one source-priority rule; there is no separate strategy enum.
Principals compose `StatementSourceMixin`, whose per-row `source_kind` contract
classifies eligible sources; `set()` refuses missing or unknown classifications.
Statements retain the assertion-time classification across source edits and
deletion. The declaring addon passes `StatementRecords(link="integrate.RecordLink")`
(and `model_name=` when `<Subject>Statement` is already a domain model; a money
column copied without its currency column becomes a plain decimal on the statement);
the base declaration defaults to `None`, and the named model composes `StatementLinkMixin`: its
`authoritative` fact (`SyncStream.authoritative`, read through `RecordLink.stream`)
places a SYSTEM_OF_RECORD source ahead of PERSON in every aspect listing both
kinds, for normal-rank statements only. `SyncStream.objects.set_authoritative`
flips it and re-derives the linked subjects. Declare compiler projections at the
owning integration; serving code does not reach into source declarations.

Ordinary person saves on aspect-backed fields assert normal statements on aspects
whose precedence admits a person, including separate MTI child tables; parent
declarations keep their shared-pk owner. An aspect whose precedence leaves PERSON
out belongs to its native owner: a person's save writes those fields as an ordinary
save. A person's create states no group whose values are all empty; clearing a
field later is an edit and is stated. Confirm or dismiss stay explicit.
Consumers contribute domain aspects through the descriptor's abstract donors: the
subject source's own declaration finalizes the one statement table (bases, prefix,
queryset, link) and every donor's aspects join it. `Aspect.precedence` owns source
ordering. Preferred person decisions precede normal source ordering; a winning
preferred no-value clears the aspect's operational values so a dismissal sticks.
Ordered `PairAspect` carriers assert symmetric facts. The merge action chooses
the survivor; survivorship never moves records. See [parties](../../addons/angee/parties/README.md).

Parties bookkeeping follows its writer's transaction contract: IAM's person
factory links the party inside its own transaction
([`person_created`](../../addons/angee/iam/events.py) fires before commit, so a
linkage failure rolls the account back), while a foreign write that only
enriches parties treats that work as best effort — deferred with
`transaction.on_commit`, logged on failure, never blocking the foreign write.
IAM stores the canonical person email:
[`UserManager.normalize_email`](../../addons/angee/iam/models.py) owns the rule
and every save that writes `email` applies it, so lookups and the person-email
unique constraint compare the stored column without SQL transforms.

[`MergeableMixin`](../../addons/angee/base/models/merge.py) owns operational redirects, actor
admission and the system fold. Models declare domain refusals and carry/retire
hooks; pointing rows declare `MergePolicy` (an edge's `merge_policy`, a key's
entry in `merge_policies` where its `on_delete` implies the wrong one) and their
collision contract. Retained
references include their historical versions. Redirects flatten in the same
changeset and protect their survivor from deletion. `canonical()` selects
operational identities; statement querysets use `applied()` for winning value
assertions. Source-copy identity remains reversible pair evidence and never
chooses a transport or moves channel occurrences.

[`HistoryMixin.history_relations`](../../addons/angee/provenance/contrib/angee/history.py) declares owned
reverse-FK collections, such as dashboard widgets or project milestones. These
rows keep their own history and read gates; document history groups their deltas
with record identity. `history_tables()` is the one concrete-table inventory.
`ChangesetQuerySet.touching(record)` and its inverse `records_touched()` own
document membership, including MTI, pairs and generated statements. Projections
consume these scopes instead of rediscovering documents from history rows.
`HistoryMixin.admitted_reverts(changesets)` owns complete-boundary admission;
`Changeset.revertable(record, actor=...)` then checks the record's declared
`history_revert_permission`. An entry may revert from that record only when its
complete write boundary belongs to the document, or it is the record's
operational merge.
Changes spanning independent documents require their wider owner. Native FK
relationships order restoration of missing rows. Hierarchy parents are ordered
also when present, so restoring an earlier tree does not create a temporary cycle;
other existing rows keep their normal update order. A record task uses its own
`RecordRefMixin` subject and declared `projects/task` permission arms; chatter is
an independent capability.

The [Changeset](../../addons/angee/provenance/models.py) owns atomic pre-boundary
revert and deterministic lossless export. Closing a boundary writes one bookkeeping
row; `document()` computes and records the hash through the one hash owner.
Closure publishes a change; committed projections read history independently
through its scopes. Revert uses native history ordering and
diffs, refuses later touches, retains withdrawn/superseded statements, and reuses
trash for created subjects. Without trash, retained references block removal.
Restoring database state does not undo HTTP effects; record sync detects the
corrected local projection under its existing write-back policy. Archive callers
use an explicit system boundary; serving exports retain subject authorization and
gated-field redaction. No archive sink or history-pruning policy is introduced.

Every captured write runs inside a [changeset boundary](../../addons/angee/provenance/context.py);
strict mode refuses one without it. Entrypoints own boundaries, one per unit of
work, and nothing else opens a `fresh` one except a deliberately separate logical
write (merge, revert, seed): HTTP requests through `ChangesetMiddleware` on the
stack's own sync or async path; WebSocket connections through
`ChangesetScopeMiddleware`, composed in the native ASGI contribution seam; Celery
tasks through [`angee.provenance.tasks.Task`](../../addons/angee/provenance/tasks.py); management
commands through the native launcher's `COMPOSE_COMMAND_CONTEXT` hooks, contributed by
[`provenance`](../../addons/angee/provenance/contrib/angee/context.py). The
[native launcher](../composer.md#flow) supplies the winning command's parsed,
read-only options at its `execute` boundary; use those options for `--database`
selection rather than parsing argv. (`serve`, `runserver` and `test` are
excluded because their requests, connections, tasks and tests own boundaries;
interactive shells keep the command boundary); a bridge live-session run through
[`run_bridge_session_job`](../../addons/angee/integrate/session_runner.py). A
boundary stores one changeset row per writing thread and actor under its shared
facts, so `copy_context().run` in a worker pool and a per-event actor loop
attribute exactly. A response status of 400 or more, a GraphQL result with
`errors` (marked by the [provenance result receiver](../../addons/angee/provenance/graphql.py)) or an
exception closes the rows as failed. Tests inherit a boundary from the root
`conftest.py`; modules marked `entrypoint_boundaries` exercise the production ones.

- **Optimistic lock:** `OptimisticLockMixin` counts instance saves. A verb that
  accepts a client's `expected_revision` passes it to `save()` and maps
  `StaleRevisionError` to its conflict result; queryset updates never bump.
- **Idempotent creation:** the concrete changeset manager owns
  `Changeset.objects.claim(scope, key, *, fingerprint)`. A new claim returns
  `None`; a matching replay returns its changeset and `created_row`; reusing the
  key for different content raises `CreationKeyConflict`. Product rows carry no
  client-key or fingerprint columns. A keyed operation opens a fresh changeset
  inside its transaction and claims before writing; nested keyed operations
  therefore receive distinct claim rows without leaving the enclosing boundary.
- **Ownership:** `owner` has one spelling. A grant root composes `OwnerMixin`,
  so `owner` is transferable access in a column and `created_by` remains
  attribution. Its Zed backs `owner` with that column and declares its transfer
  permission (`angee.E022`); its `write__owner` gate names that permission
  (`angee.E027`). No other relation is named `owner` (`angee.E045`): attribution
  used as access is `relation creator: auth/user // rebac:field=created_by`, a
  domain role keeps its own word (`user`, `organizer`, `salesperson`, `buyer`),
  and ownership is never a stored tuple. Change it through `transfer_ownership`;
  bulk release belongs only to an owning verb that already authorized it. See the
  [example notes](../../examples/addons/example/notes/permissions.zed).
- **Delegated field gates:** a definition that delegates to a parent re-declares
  the parent's field gates through that relation, as enforced for ownership by
  [the base checks](../../addons/angee/base/checks.py). Re-save a fresh, unreloaded
  instance with `update_fields`: without a loaded snapshot, a full save writes
  every column, including `owner`, and must pass every applicable field gate.
- **Owning containers:** a container composing `ItemOwnershipMixin` can own its
  newly inserted items, which then reach access through it; items name it with
  `owner_container` (`angee.E025`) and ask
  [`OwnerMixin.container_owns_items()`](../../addons/angee/base/models/ownership.py) for its policy.
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
  itself. Managers
  are a positive arm beside the exclusion, never an exclusion inside it: rebac's
  named-grant scan cannot compile an exclusion that holds another
  ([tasks](../../addons/angee/projects/permissions.zed)).

- **State columns are `StateField`; guarded changes go through transition methods, never direct assignment.**
  [`StateTransitions`](../../addons/angee/base/models/transitions.py) owns one transaction around
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
- **Give a model an opaque public id by mixing in `SqidMixin` and declaring
  `sqid_prefix = "abc_"`** — the one fact that varies per model. The shared
  `angee.base.fields.SqidField` reads that prefix in `contribute_to_class`; don't
  re-declare the column. The field is NULL-safe by design, because a sqid can be
  selected through a nullable join where `django_sqids.SqidsField` crashes on a
  NULL (REBAC `// rebac:field=` arrows run over nullable FKs).
- **`AngeeModel` managers/querysets must keep the canon.** If a model customizes
  `objects`, its queryset class must derive from `AngeeQuerySet`; otherwise
  shared methods such as public-id lookup, actor scoping, and elevated reads drift
  between models.
- **MTI down-casts use `angee.base.lineage.concrete_child`.** The parent-link
  accessor and any query prefetch identify the child; callers supply their
  actor-scoped queryset when that lookup is permission-sensitive. A parent
  row never grows a human-label kind column to mirror its child model.
  Clients read a parent's exposed children from its resource metadata's
  `concrete_kinds` ([projection](../../addons/angee/graphql/data/metadata.py))
  instead of re-deriving inheritance.
- **Deletion refusals have one model owner.** Override
  [`AngeeModel.delete_blocker()`](../../addons/angee/base/models/base.py) with a public-safe
  message. [`DeletePreview`](../../addons/angee/graphql/deletion.py) reports each
  distinct refusal before deleting, without row counts or hidden identities.
  A model overriding the hook must bind a `pre_delete` receiver enforcing that
  same rule on every deletion path; an instance override cannot protect cascades.
  Receivers remain authoritative when state changes after preview and ensure
  Django collects those models rather than fast-deleting them.
  When deleting a model needs locks in a domain order (a parent before the row),
  override its [`lock_for_delete()`](../../addons/angee/base/models/base.py); its `pre_delete`
  receiver and every confirmed-delete caller of that model then lock through the
  hook, callers only after their permission preflight.
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
  retained rows. This is a modelling rule, not a mechanical system check. Generic collection-update guards share
  [`ClosedUpdateQuerySet`](../../addons/angee/base/querysets.py). Its closed
  `update`/`bulk_update` rule and protected `owner_update` live together; compose
  that guard before downstream policy and native capture. Bank identity-style
  models can reuse this edit policy while retaining their own insert/delete rules.
  Retained models compose [`AppendOnlyModel`](../../addons/angee/base/models/append_only.py) and
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
  not reopen generic mutations or replace model/FK invariants. `AuditMixin` dispatches
  collector-owned nulls through the selected queryset; a closed-update policy
  uses its protected owner verb, preserving every downstream guard and capture.
  It uses its serializable
  `retained_set_null` FK policy to schedule nulling inside Django's delete
  transaction under `system_context(reason="delete.retain")`. Removing that
  retained edge is part of the authorized target deletion, for tracked and
  untracked referencing rows alike; tracked rows retain changeset history.
  Native `SET_NULL` has no collector-origin hook at `QuerySet.update()` and
  still follows the queryset's ordinary authorization. References needing this
  system policy must declare `retained_set_null` until an upstream collector
  hook can carry that origin without elevating ordinary FK edits.
  Bounded diagnostics and persisted model labels use `DiagnosticTextField` and
  `ModelLabelField` from [`angee.base.fields`](../../addons/angee/base/fields/__init__.py), so
  instance saves and queryset writes share their storage rules.
  [`MessageSubtype.model_label`](../../addons/angee/messaging/models.py) and
  [`ThreadNotification.failure_reason`](../../addons/angee/messaging/models.py)
  compose these field owners. A deployment crossing the
  [upgrade cutoff](#migration-history) must preflight
  unknown/colliding subtype labels and retained diagnostics before canonicalizing
  them on its pre-refactor snapshot, preserving IDs/keys, global blank semantics
  and retained text. Those completed transition bodies are absent from current
  source. The persisted subtype business key `activity_done` remains current;
  renaming it requires mapping subtype rows, follower choices and callers together.
  Never replace these rules with a database trigger or function.
- **Row locks must keep the SQLite floor.** Call the Angee queryset/manager
  `lock_if_supported()` method to name lock intent, delegating `of`,
  `skip_locked` and `no_key` to `select_for_update`.
  Django owns write routing and backend feature checks: SQLite emits no lock SQL,
  including when `of` is requested. Backends
  supporting row locks still enforce their supported lock options. Do not set
  private queryset write state or duplicate Django's feature checks.
- **Hierarchy rows are created through `save()` or `HierarchyQuerySet.create_forest`.**
  A batch (a cloned page tree) is one INSERT and one parent-and-path UPDATE
  whatever its depth, and each row records one creation version, path included;
  `save()` hooks do not run, so the caller sends the lifecycle signals it owes.
- **A `HierarchyMixin` consumer declares its scope fields — the mixin never probes
  by column name.** A subtree that must stay inside a tenant or other scope
  declares `hierarchy_scope_fields = ("scope",)` (a `ClassVar` tuple; FKs compare
  by stored id); the mixin rejects a parent that differs on any listed field
  and refuses a scope edit that would strand existing children. It is generic and iam-free — there is no scope-field-name
  fallback, so a scoped tree that omits the declaration silently accepts a parent
  outside its scope. Compose `HierarchyQuerySet` before every other queryset
  guard, including on secondary managers; the base `angee.E021` check rejects
  ordering or `update` overrides that path maintenance would bypass.
  `StateField` transitions guarded by `save_state` get an
  optimistic-concurrency guard for free: the committed source is re-read under the
  same lock before the write, so a lost race raises `TransitionNotAllowed` instead
  of double-applying (e.g. double-posting a ledger).
- **`MembershipDecisionMixin` owns plain roster decisions, not provenance.**
  A subclass that needs REBAC side effects overrides the transition; never add
  REBAC writes to the shared mixin.
- **`EncryptedField` keys are bound to `model._meta.label_lower` plus field
  name.** Renaming a model/app/field changes the derived key. Plan
  `ANGEE_FERNET_KEYS`/`MultiFernet` rotation before such a rename, and treat one
  corrupt row as a row-local unreadable value, not as a reason to break list
  queries.
- History snapshots retain stored values even when the actor's view is redacted.
  [`ModelHistory`](../../addons/angee/provenance/contrib/angee/history.py) gives native history construction a
  detached snapshot and records deletion inside the delete transaction before
  the source row disappears; it never unredacts the caller's instance.
- [`HistoryMixin`](../../addons/angee/provenance/contrib/angee/history.py) excludes `GeneratedField` and its
  subclasses from historical models because their expressions belong to the live
  row. A queryset update that writes only columns the history excludes (a
  counter), with at most auto-now stamps, records no version. A multi-table
  child's save records the child's history only; its version carries the inherited
  columns, and its revert restores them. A record's chatter thread and edge are its
  bookkeeping: they revert under the record's `write`, and undoing the record's
  creation tears them down with it. Provenance autoconfig enables the native
  `SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD` setting, so simple-history
  allocates a separate nullable text change-reason field for each historical
  model; inherited tracking must never share mutable field instances across models.
  This is an intentional project-wide default, including consumer and third-party
  `HistoricalRecords` declarations. Existing histories that used the default
  `CharField(max_length=100)` need a schema migration to `TextField`; declarations
  with an explicit `history_change_reason_field` retain their chosen field.

Angee's [`ModelHistory`](../../addons/angee/provenance/contrib/angee/history.py)
requires native `use_base_model_db=True`: snapshots, Changeset references and
replay stay co-located with the live model. Explicit `False` is refused. Generic
[`angee.provenance.history.ModelHistory`](../../addons/angee/provenance/history.py)
retains its native routing contract.

### Migration history

- **Each project owns durable native Django migration history.** After compose,
  native `makemigrations` binds applicable addon transitions before autodetection.
  Review and commit the result. `compose --check` checks generated artifacts;
  `check_migrations` checks native history. Packaging creates archives and
  provisioning applies history without authoring it. Released names and bodies
  are immutable; corrections use forward migrations and retain the original
  chains while partially upgraded databases remain supported.
- **History retirement/reset requires an explicit support cutoff.** Native
  `retire_history` and `reset_migration_history` own their applied-history and
  dependency proofs. Read the installed command before choosing options. Preserve
  Python imports still used by live histories through narrow compatibility aliases.
  [Migration procedures](../../.agents/skills/angee-runtime/references/migrations.md)
  own the operational cautions; routine startup never retires history.
- **Removing a field, model or donor addon whose storage holds data needs a
  declared cutover.** `makemigrations` refuses the autodetected drop through the
  [composer's drop guard](../composer.md#addon-owned-runtime-migrations). An
  addon that stays installed declares a runtime migration that preserves or
  retires the data and removes the field or model; an empty column needs
  nothing, and neither does a table holding only rows its own resource files
  seeded once the addon names the model under `retired_models` in its
  `pyproject.toml` `[tool.angee.resources]`.
- **`MIGRATION_MODULES` may be assigned during app populate only for generated
  runtime apps.** That exception belongs to composed settings/runtime boot; do
  not use it as an addon-local shortcut or a way to hide source-model migration
  state.
- **A rename of persisted references or permission namespaces is an upgrade
  path, never startup work.** The release that changes them declares which old
  state needs data migration and which reconciliation follows it; a fresh
  installation inherits no older deployment's repair procedure.
- **Data migrations are system operations.** A `RunPython` body reads a
  REBAC-scoped model through `model._base_manager.using(db)`: a manager with
  `use_in_migrations = True` rides into the historical model, so its `objects`
  stays actor-scoped there and raises under strict mode.

### Resource loading

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
  Other relation columns name rows by `<addon>.<xref>` (a list or
  comma-separated string for many-to-many). Content types are never ledger rows,
  so a foreign key or many-to-many to `contenttypes.ContentType` takes Django
  model labels instead (`applies_to: [parties.Handle, parties.Person]`),
  resolved by [`ModelLabelField.get_model`](../../addons/angee/base/fields/__init__.py); an
  unknown label fails its row. Row hashes cover the seed's labels, never
  content-type ids, so a seed hashes the same in every database.
  Source omission and explicit null must remain
  distinguishable through dataset normalization.
- <a id="seeded-transition-state"></a>**Transition-owned state in a seed is an initial value, applied on create and never on update.**
  Seed initial state through model construction.
  This covers only fields guarded by a
  [`StateTransitions`](../../addons/angee/base/models/transitions.py) declaration; the
  [resource loader](../../addons/angee/resources/loader.py) validates seeded state
  before discarding it on updates. Companion fields written by transitions
  (an agent's `workspace`, `service`, `runtime_status`, `last_error`, or receipts
  such as `submitted_at`) remain ordinary seed fields and are not protected.
  Seeds must omit those fields to preserve their live values.
  Unchanged rows with hashes from the previous state-inclusive rule migrate
  only their ledger hash; changed seed values or keys still trigger import.
- **Demo resource tiers are additive across installed addons.** A deployment that
  needs demo rows but not one permissive seed must exclude that exact entry with
  `ANGEE_RESOURCE_EXCLUDED_ENTRIES`, keyed as `addon.name:resources/path.yaml`;
  do not edit the addon seed or fork the loader.
- **A seeded row that clients select carries a resource-assigned stable key.**
  Clients and tests select it by that key, never by a mutable display name.

### Development server

- **The native launcher owns the Django reloader parent.** Rendered `manage.py`
  calls `django_compose.management.execute`, which composes in a fresh process
  before a reloader child. Django owns imported/input-file watching and reload
  decisions. [`angee.server`'s runserver override](../../addons/angee/server/management/commands/runserver.py)
  serves the child with Uvicorn and closes its ASGI lifecycle on shutdown. Do not
  add a second `uvicorn --reload` watcher or start a server outside the operator.
- **Serving consumes artifacts.** GraphQL owns SDL and metadata through its
  [SDL owner](../../addons/angee/graphql/sdl.py); its ready-phase `SchemaIntegration`
  contributes SDL/metadata to native compose. `schema --check` reads that snapshot;
  codegen remains a separate lifecycle step. The compiler owns generated-tree
  publication and receipt verification. [Checks](../checks.md#composition-and-schema)
  owns the ordering; neither a web request nor ASGI mounting repairs output.
- **Session and CSRF cookie names are project-scoped.** Localhost ports do not
  isolate browser cookies between stacks; preserve the project template's names.

### Addon WebSocket endpoints

Contribute socket protocols through `AppConfig.compose_asgi()` and compose
`RebacChannelsConsumerMixin` to pin the cookie actor before creating tasks.
The [GraphQL Channels owner](../../addons/angee/graphql/asgi.py) owns Origin trust and Django session
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
  `expected_revision: Int`; a stale value fails with `STALE_REVISION`.
  With the [provenance write adapter](../../addons/angee/provenance/graphql.py) installed,
  `insert_<resource>_one` also accepts `idempotency_key: String`; replaying the same
  actor/resource-scoped key and content returns the readable original, and
  changed content fails with
  `CREATION_KEY_CONFLICT`. Neither token belongs in `_set` or `object`.
  The [Hasura adapter](../../addons/angee/graphql/data/hasura.py) composes native
  write backends and their declared arguments; provenance owns creation claims,
  and the model owns optimistic locking. A host without provenance exposes no
  idempotency argument.
- **Permission answers reuse the authorization owner.** Declare a type's
  `permissions` through [`permissions_field(names)`](../../addons/angee/graphql/capabilities.py);
  it reports only the declared Zed permissions held by the current actor,
  validates names at schema build and batches list evaluation in SQL.
  Server predicates compose the same owner's `held_permissions(record, names)`.
  View-as therefore reports the target's permissions. Clients consume these
  answers; they never reconstruct authority from roles or identity.

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
- **Lines are owned parts, written as the user.** Declare `HasuraLines` only for
  rows that exist as part of their parent and save with it; associated records
  stay their own resource. The child writes run elevated because the parent
  write authorizes them, which never makes them system work. A child whose
  invariants separate users from system writers composes
  [`RowLockMixin`](../../addons/angee/base/models/locks.py): `locked_fields()` names what its
  state locks, user writes may not change those fields, delete the row or create
  one. Its default manager composes `RowLockQuerySet` so native collection
  writes enforce the same owner rule; the declared base manager retains that
  guard. A collector may remove locked children only through the model's explicit
  owning-parent cascade contract. `HasuraLines` checks the same lock before each
  intentionally elevated child write. Its node projects `locked_fields`, advertised as the lines' `lock_field`. Never
  guard a child on `is_sudo()` alone; [`Stage`](../../addons/angee/work/models.py)
  is the reference. A nested insert only inserts beside children the parent's own
  save provisions.
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
- **Create writes validate the final input contract.** `hasura_model_resource`
  creation prepares one instance and `full_clean`s it before persistence; the
  locked Strawberry version then persists through `manager.create(**kwargs)`, so
  the prepared instance is not the saved instance. Model and input defaults must
  agree: required fields and defaults come from the final input contract, never
  a second list in a consumer form; model-owned defaults belong in `clean()` and
  `save()` ([`Cadence`](../../addons/angee/nexus/models.py)); a required field
  that `clean()` defaults declares `blank=True` so field validation defers to
  that owner while the column stays non-null; and an optional create-input field
  over a non-null column defaults to `strawberry.UNSET`, never `None`. Factory
  invariants shared by ORM and GraphQL creation belong on the queryset's
  cooperative `insert()`, which ordinary `create()` composes; creation that needs
  preparation before validation dispatches through
  [`AngeeHasuraWriteBackend`](../../addons/angee/graphql/data/hasura.py) to a
  model manager factory ([`IntegrationWriteBackend`](../../addons/angee/integrate/schema.py)
  composes that seam), keeping native mutation envelopes, metadata and form
  submission.
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
  [`DomainError`](../../addons/angee/base/exceptions.py) with a stable `code`; the
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

### Schema lifecycle and change publication

- **Live timelines read owners; notifications invalidate reads.**
  [Timeline kinds](../../addons/angee/timeline/kinds.py) contribute actor-scoped
  source querysets. The [read owner](../../addons/angee/timeline/reading.py)
  combines their common columns with Django's `union(all=True)`, applying
  permissions, attention, folding and cursors in each branch before the shared
  page limit. Display payloads are hydrated only for selected source rows.
  There is no event projection table or backfill requirement. Folding hides a
  child only when a readable parent target occurs on the same record; counts
  and attention use that same scope, independent of the page's filters.
  [`publish_change` / `change_published`](../../addons/angee/graphql/publishing.py)
  delivers owner observations inside the transaction. The
  [timeline observer](../../addons/angee/timeline/events.py) captures affected
  record identities, including old membership before a save or delete, and
  uses the declared record feeds to invalidate reads after commit. Django's
  callback queue owns rollback; no projection queue or source-row lock is needed.
  Observation never enables a private source's transport feed. Kinds name the
  source fields they read, so unrelated partial saves skip dependency discovery;
  inherited sources and the core's bounded `bulk_updated` batches use the same
  seam. A verb completing a conditional or bulk write publishes after its
  related rows are ready. `change_batch_targets` may coalesce publications;
  local observers still receive original bulk batches where needed.
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
  Bare app population and `manage.py --help` stay build-free. Writer-only processes that call `django.setup()` without running
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
  [`angee.graphql.data.contracts`](../../addons/angee/graphql/data/contracts.py) owns the transport-neutral
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
  widget keys in `angee.graphql.data.resource_fields` with the matching
  frontend renderer; resource callers declare fields, not ad hoc widget strings.
  Status shape is likewise a field-owned projection fact: a field or Strawberry
  surface declares `angee_status_display`, metadata emits `status_display`, and
  the wire alias is `statusDisplay`.
- **Type extensions retain native optimizer hints.** Declare hints on the
  contributed field; [`AngeeSchema`](../../addons/angee/graphql/schema.py) exposes
  the composed definition to optimizer consumers without mutating shared addon
  declarations, so a narrow list selection costs the same at any row count.

### Direct record access

Direct grants and ownership transfers acquire their model's cooperative
`lock_for_record_access` hook before fresh subject admission. An addon with a
domain lock order acquires its prerequisite rows there and delegates; validators
stay usable independently of a write transaction. See
[`AngeeModel`](../../addons/angee/base/models/base.py) and the
[Proposals owners](../../addons/angee/proposals/models.py).

[`RecordRefMixin`](../../addons/angee/base/models/refs.py)
owns the generic pointer's model contract, accepted targets, merge policy and
deletion policy. [`angee.base.refs`](../../addons/angee/base/refs.py) owns the underlying pointer
identities and public reference values. Its public projection uses the minimal
[`model identity contract`](../../addons/angee/base/models/identity.py), without
importing the composed model toolkit.
[`RetainedGenericRelation`](../../addons/angee/base/fields/relations.py) owns the
native reverse relation's exclusion from automatic collection.
Each edge declares `on_target_delete` as `CASCADE`, `PROTECT`, or `SET_NULL`; a
model needing row-aware cleanup implements `teardown_for_target(target)`. The
foundation target-delete receiver invokes that one declaration for instance and
queryset deletion paths.
[`RecordReferenceNode`](../../addons/angee/graphql/relations.py) and
`with_record_reference_access` project it into GraphQL under the current
reader's scope. A hidden target yields null identity fields; its model and ID
must also stay unavailable through resource filters and aggregate counts. Addon
resources declare their record-reference filter axes through the shared Hasura
resource owner instead of reimplementing this projection.

**Polymorphic edges authorize their target in the schema.** Back one relation
per target type with the edge's `GenericForeignKey`, contributed by the app
that owns the type (`relation task: projects/task // rebac:field=target`) or
declared by the edge's own addon when it already depends on the type's, and
write the edge's `create`, `read` and `delete` over them; the type's model must
be its own canonical model and its identity its primary key (`rebac.E009`).
Store targets with `rebac.generic_target(record)`, create and delete edges
under the actor, and list a record's edges with the scoped edge queryset
filtered by `generic_target(record).lookups(Edge, "target")`. Move an edge by
deleting and recreating it; the library refuses a retargeting save. A type
without a relation is refused under an actor, and an edge that accepts only
some types derives that set from the schema through
`RecordRefMixin.declared_target_models` (`declares_target` for one type)
instead of listing it again. Resource metadata projects the inverse as
`recordEdges`, so a surface listing one edge applies only to the record types
that can carry it; a resolver does not re-decide that per request. Each
edge's own `permissions.zed` states its rule:
[storage](../../addons/angee/storage/permissions.zed),
[knowledge](../../addons/angee/knowledge/permissions.zed),
[projects](../../addons/angee/projects/permissions.zed) (a binding takes
share on the project and the type's grant authority),
[portfolio](../../addons/angee/portfolio/permissions.zed),
[tags](../../addons/angee/tags/permissions.zed) (an assignment also needs
the tag's read) and
[messaging](../../addons/angee/messaging/permissions.zed) (a const relation on
the row's role keeps the arms off chatter edges). Evidence and provenance edges
whose access follows their owner (workflow subjects, step records and watches,
decision records, extraction sources, integrate record links, currency-rate
contexts) take no target relations: the owner's admission gate checks the
record at write time, and the edge still stores `rebac.generic_target`. Only
an edge that names rows outside REBAC (a chatter thread on an ungated host, an
import record link to a plain sink) stores
`angee.base.refs.generic_pointer_target`, which keeps Django's own identity
for such a row. Compose `ThreadedModelMixin` and reverse `GenericRelation`s on
the same canonical ancestor either way.

Models opt into direct sharing with `rebac_grantable`, mapping each relation to
the permission required to manage it. The model remains the policy owner:
`direct_record_access(relations=...)` lists only the caller-authorized declared
subset, and `validate_record_access_target()` enforces model-specific target
rules for listing, options, grants, and revocations. For example, Workflow
accepts grants only on its lineage head; GraphQL must not infer that rule from
Workflow fields. A `validate_record_access_subject()` override that keeps one
user out of a record's holders raises the foundation-owned
[`RecordAccessSubjectRefused`](../../addons/angee/base/exceptions.py) when
[`subject_reaches_user`](../../addons/angee/base/permissions.py), the one predicate for
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

Native package metadata owns addon identity and requirements; `pyproject.toml`
ships capability declarations under `[tool.angee]`. AppConfig owns Django's
identity customization, registration and lifecycle. Each
capability owner interprets its own declarations and conventional defaults; it
never creates a separately configurable package-fact mirror. Model/manager behavior
stays with the native class, and composition lifecycle stays with the compiler.
Keep the exact authoring forms in owner docstrings and the native package
contract, rather than duplicating them in this guideline.

Before decomposing backend code, classify each fact by its Django owner:

- Persisted choices live beside the model field, usually as model-owned
  `TextChoices`.
- Row-set behavior lives on managers and querysets.
- Instance behavior lives on model methods and properties.
- Addon declarations live in `pyproject.toml`; capability owners resolve their paths from Django `AppConfig.path`.
- Management commands parse arguments and dispatch to the owning model, manager,
  service, or composer function.
- Compatibility facades exist only for an explicit compatibility promise.

The project settings contract declares project facts; Angee owns Django
composition wiring. Anchor project defaults to `BASE_DIR`, never the current
working directory, and use Python settings only for facts that need Python.
Follow [settings bootstrap](../composer.md#settings-bootstrap) and
[autoconfig](../composer.md#autoconfig) for the current loading and merge owners.
`django_compose.conf.configure` owns settings order and the immutable snapshot.
[`angee.base.conf.configure`](../../addons/angee/base/conf/__init__.py) loads
bounded source/deployment overlays before delegating once. The compiler owns
native migration mappings and verified serving output. An enclosing stack never
silently supplies settings to a nested project.

Declare autoconfig `SETTINGS` and derived `settings()` with source-owned types,
including nullable values and normalized `:append` contributions. Native
compiler/provenance typing integrations consume these contracts; do not patch
source models, compiler output or a filesystem history cache to supply types.

Keep `angee.*` a native PEP 420 namespace. Leave namespace roots such as
`addons/angee/` without an `__init__.py`; installed distributions contribute
portions without a host namespace shim.

Avoid `__all__` unless a module has a concrete star-import or compatibility
requirement. Public API should usually be obvious from module names, object
names, and docstrings.

### Verb eligibility projections

Record controls consume eligibility from the verb owner. Task audience domains
extend [`Task.visibility_blockers`](../../addons/angee/projects/models.py): each
SQL condition names its validation exception. The locked visibility verb and
optimized `allowed_visibility` projection share those conditions and native
permission scopes. Hidden domain facts stay inside the projection query.
Message writers must still preserve publication invariants under the task lock.

Task hand verbs follow the same shape. [`Task.action_blockers`](../../addons/angee/projects/models.py)
names each row condition refusing a verb with its validation message; contributors
extend it and `hand_actions` through `super()`, as
[work's stage rules](../../addons/angee/work/models.py) do. Each verb calls
`validate_action` after its idempotent replay, and the `task_actions` projection
batches the same conditions with the verb's `write` scope. A control shows where
its verb would act (the row's status or stage) and where `task_actions` offers it.
A round's lift reads `can_open`, which composes the same
`Round.opening_phase_condition` its verb checks.

Decision admission, answers and withdrawal belong to the
[decisions manager](../../addons/angee/decisions/managers.py). Asking owners
consume its verdict; controls consume the owning verb's eligibility.

### Integration and worker contracts

[`angee.integrate`](../../addons/angee/integrate/README.md) owns the shared record
protocol. Backends declare independently ordered stream partitions; domain
managers retain identity and ingest policy. Event feeds are append-only and
idempotent by domain identity, so they never create replica links. Mutable record
replicas pin their own applied link history row as the comparison base.
Adapters provide comparable JSON-native local and remote mapped projections;
the driver normalizes both sides through the canonical JSON serializer before
upstream `DeepDiff` compares them with that base. `canonical_json_sha256` is the sole
source-hash owner; event feeds deduplicate through domain
identity and are never payload-hashed. Cursors contain plain finite JSON,
validated at the driver boundary.

RecordLink is a pointing row with `merge_policy = MOVE`. Merging its target
moves every link to the survivor; both remote cards receive the survivor's
projection through their existing write-back policies and remote preconditions.
`StreamDefinition` declarations own the five generation-seed knobs: `cursor`,
`reconcile_interval`, `absence_threshold`, `tombstone_retention`, and `config`.
Persisted stream rows carry runtime state; management pages do not redefine
those declaration facts.

Public-feed responding composes [held replies, reply hold, feed streams and
delivery settlement](../glossary.md#workflows) through the
[posts](../../addons/angee/posts/README.md),
[messaging](../../addons/angee/messaging/README.md) and
[thin workflow](../../addons/angee/workflows_posts/README.md) owners.
Eligibility and the locked pre-create recheck belong to the message owner;
steps call that predicate and factory. Verified webhook landing uses the same
bridge advisory lock as polling and delivery settlement.

- **The cursor commits with the records it covers.** Extract outside the database
  transaction; commit the applied page, its quarantine and the stream cursor in
  one transaction. Semantic, field-validation and record-level database data
  failures use savepoints and the link/stream inbound state owner so later records
  continue. Domain write owners truncate source display text to model-field limits;
  identifiers stay lossless or are refused, never silently truncated.
  Infrastructure failures roll back the page. Conditional remote writes happen
  outside database transactions and are
  reflected only after their response; no cross-system atomicity is implied.
- **Compare both mapped sides with their pinned applied base.** Unchanged pairs do
  nothing, remote-only changes apply, and local-only changes may write back with
  the expected remote version. Divergent changes on both sides, including a remote
  tombstone against a local edit, are unresolved conflicts: never silently choose a winner. The
  adapter locks and revalidates its local projection before applying. Identical
  edits converge under the accepted record-sync design ruling. An origin
  stamp plus the returned version/hash makes a successful write-back recognizable
  on its next pull. The link checks its complete retained reflection precondition;
  that does not replace the remote transport version fence.
- **Applied evidence has one promoter.** Adapters return the actual mapped
  payload, mapping version and dependency digest in `ApplyResult`; the driver
  alone promotes the primary link and pins the history row written for that
  application. Observation saves the source on the link without changing that
  protected pointer. History is the observation log; inbound state retains
  simultaneous and closed failures, and refused writes retain link outbox state.
  Unlinked event failures belong to the stream's `inbound` state. Mapping or
  dependency changes count as remote changes. Intermediate adapter promotion is refused and rolled back. Explicit
  `target=None` withdraws a binding; omission preserves it.
- **Prepare the entire page before singleton application.** The optional
  `prepare_page` hook acquires compound identities and targets in canonical order
  inside the page transaction, before record savepoints. It does database work
  only; all remote facts belong to extraction. Optional `on_revalidated` and
  `on_absent` hooks restore or withdraw native projection visibility in the same
  transaction as the corresponding link status. Absence hooks run only for actual
  status transitions, never repeated observations of an already-unavailable row.
- **A cursor belongs to an epoch.** An invalid/expired cursor or explicit resync
  request creates the next baseline generation. A direction or snapshot change
  also opens a new generation with the requested declaration; baseline completion belongs
  only to that generation. Retain links and native link
  history; reverify links through their generation marker. A stale peer beyond
  tombstone retention requires a baseline. Complete inventory sweeps count
  absences before confirming tombstones and preserve existing quarantine.
- **Read-once requires a snapshot source and closure.** A declared snapshot with
  no reconciliation interval closes after its baseline. A closed stream retains
  evidence but does no transport work and ignores cursor expiry; an explicit
  resync creates a new generation.
  Recurring event feeds without reconciliation remain open. Declare the source
  through [`StreamDefinition.snapshot`](../../addons/angee/integrate/states.py),
  mirrored on `SyncStream.snapshot`; backend-private `config` does not own cadence.
  Do not interpret a missing interval alone as read-once.
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
  and inbound resolution; a successful parent alone does not resolve them.
- **Quarantine is not a work queue.** Stream-cycle rescan re-reads due replica
  identities through `BridgeImpl.read_keys` when `supports_identity_reads` is
  declared, including tombstones for missing remote keys. It composes the same
  transactional apply path while preserving the cursor. Only adapters without
  this operation fall back to a baseline, with that fallback recorded in
  inbound details. Event feeds skip rescan. Semantic failures back off
  between attempts; conflicts await explicit resolution and never auto-retry.
  The shared driver owns budgets, repeated-page detection and partition
  concurrency.
  Workflow execution and durable scheduling stay with their existing
  owners; integrate must not import workflows.
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
- **A long-lived session task stops within one reconciler tick.** A task that
  outlives the tick (a live connection) runs on a dedicated queue consumed by a
  threads-pool worker, so queue isolation — not a time limit — is its
  protection. Its loop wakes on a bound shorter than the tick and exits when the
  persisted desired-state no longer wants it, when the worker is shutting down,
  or when it no longer holds its advisory lock; the reconciler enqueues with
  `expires=` of one tick so an absent or saturated worker never accumulates a
  backlog. `angee.integrate.session` and `angee.integrate.tasks` are the
  references.
- **Bridge scheduling follows the final MTI kind.** A child capability may
  extend another bridge (a public Feed extends Channel); the
  [`Integration.concrete_type`](../../addons/angee/integrate/models.py) identity
  selects the one bridge row to schedule and route. Parent-scoped message access
  stays on the declared Channel relation; child access inherits through its
  REBAC parent arrow.
- **An asyncio vendor SDK owns its loop on the live session's connection
  thread; the task thread remains the only persistence owner.** Task-thread
  work that must call an async vendor hook schedules the coroutine onto that
  owning loop with `asyncio.run_coroutine_threadsafe` and waits with a finite
  timeout.
- **An Integration child's second intent axis is the reconciler's to close.** A
  child declaring no `sqid_prefix` of its own is an `int_…` row, so the generic
  lifecycle actions (`integrate/schema.py`, `IntegrationActionMutation`) move it
  knowing only the one axis every Integration has. Two independently writable
  intent axes need a declared precedence and one owner that reconciles the
  other: the child's reconciler selects on the lifecycle and drives its own axis
  (a live desired-state, a subscription) from it, reconciling only when the axes
  disagree, and its runtime guards honour the lifecycle itself.
  `ensure_bridge_sessions` in `angee.integrate.tasks` is the reference.
- **A latching gate needs a reset the operator's verb owns.** A reconciler that
  skips rows on `runtime_status=ERROR` is re-enabled only by the repair verb
  clearing that status itself, never by a lifecycle edge as a side effect. Keep
  the gate on the shared `runtime_status`, not a private health key the generic
  verbs cannot clear.
- **Periodic tasks accept an optional `timestamp`.** Static Celery beat ticks
  call without it; tests and scheduler backends may inject a Unix timestamp.
- **TLS trust is an environment concern, not a per-call one.** Which CA roots we
  trust is owned by the runtime, set once — never threaded as an `ssl_context`
  through each outbound HTTPS call. Outbound code uses the stdlib default context
  (`ssl.create_default_context()`), which OpenSSL resolves against the system
  trust store and honours `SSL_CERT_FILE`/`SSL_CERT_DIR`. An environment that
  lacks a CA store is repaired in that environment
  ([environment pitfalls](../../.agents/skills/angee-tests/references/environment.md)),
  never by adding `certifi` plumbing to call sites. Backend outbound HTTP has one
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

### Workflow contracts

- **Root subject lifecycle belongs to the record.** Models opt into
  [`RunSubject`](../../addons/angee/workflows/subjects.py) to admit starts and
  retries and settle the first terminal transition. The engine locks the run
  before the subject and keeps these database-only hooks in its transaction;
  UI cancellation, failures and timeouts use the same terminal writer. Bridge
  cycles compose this contract through
  [`SyncCycleBridge`](../../addons/angee/workflows_integrate/sync.py), while
  integrate retains scheduling, queue tokens, advisory locks and dispatch
  compare-and-set. Consumer bridges supply scope locks and an input snapshot;
  they do not create another subject lifecycle receiver. Run-owned auxiliary
  resources use the engine's [`run_stopping`](../../addons/angee/workflows/signals.py)
  cleanup seam for terminal transitions. Deferred cancellation schedules the
  transition after the caller releases its locks. Decorate receivers with
  `isolated_run_cleanup` before connecting them; they must be idempotent.
  The decorator isolates each receiver's database cleanup in a savepoint and logs its
  failure without interrupting the terminal write or other receivers.
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
- **Reviews retain one current decision per step.**
  [`DecisionStep`](../../addons/angee/workflows/decision_steps.py) asks through
  `ctx.ask(request, state=...)`; `StepRun.decision` retains the question.
  The answer wakes the step after commit, with a tick sweep for missed delivery.
  A continuation may ask again after an answer when its reviewed facts changed;
  the previous question remains a native execution record associated with its run.
  Open questions cannot be replaced. Check the owning record's revision before
  applying an old answer; a fresh question must not certify changed facts.
  The resumed body applies the chosen alternatives through record write owners
  as the run actor. A failure rolls the body back and retains the answer for
  retry. Non-workflow askers apply synchronously inside the
  verdict transaction; refused application rolls the answer back.

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
- **Never name an addon module after a third-party top-level package it imports.**
  `unittest` discovery inserts the discovery-root directory onto `sys.path`, so an
  addon's `mcp.py` that does `from mcp.server… import …` becomes an importable
  top-level `mcp` that shadows the real package — `ModuleNotFoundError: 'mcp' is not
  a package` during a test run, while a single-module run and `manage.py check` pass.
  Name such a module for its role, not the library. The conventional MCP tool
  contribution lives at `mcp_tools.py`, not `mcp.py`.

## Checks

Commands, execution roots and required checks live in
[Python and composition contracts](../checks.md#python-and-composition-contracts),
including the [owner search](../checks.md#owner-search) scans that precede a new
backend abstraction.
When to run which check, which test area guards a new or extended seam, and the
proof obligations the moved pitfalls carry belong to the `angee-tests` skill:
[verification procedure](../../.agents/skills/angee-tests/references/environment.md#verification-procedure)
and [seam guards](../../.agents/skills/angee-tests/references/environment.md#seam-guards).
