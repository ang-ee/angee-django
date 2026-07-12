# Glossary

Shared vocabulary for working in the Django / React Runtime. Terms are defined once here;
other docs link to this file instead of redefining them. This is a living
document — add a term when it first needs explaining, and keep each definition to
the smallest accurate statement.

## Composition

**Angee** — a thin composition framework that binds proven libraries into one
deterministic product surface. It owns the seams, not the concerns.

**Addon** — the unit of capability. An addon declares a contract (source models,
operations, routes, slots, resources) that the composer assembles into a project.
Everything, including the framework core, is an addon.

**Framework addon** — an addon that is part of Angee itself. The framework core is
the foundational framework addon; other base addons ship with Angee and build on
it. Inherited by every project downstream, so held to the highest bar.

**Consumer addon** — an addon written by a product team for a specific project,
built on top of the framework and base addons.

**Composer** — the build-time tool that turns addon contracts into a runnable
project: it reads each addon's source models and declarations and emits the
concrete Django apps, permission schema, and other `runtime/` artifacts.
Composition happens at build time — nothing is monkey-patched or registered at
runtime.

**Project settings** — the `settings.yaml` and optional Python settings module
selected by `ANGEE_PROJECT_SETTINGS`. They declare root apps with Django
`INSTALLED_APPS`, addon directories, and project-specific overrides. Angee's
composed settings module turns that contract into the running Django settings.

**Project** — a runnable product: project settings, consumer addons, frontend
entrypoints, and generated runtime output. A project is scaffolded from a
**project template** and owns its repository root, including the canonical
`.copier-answers.yml`.

**Project template** — the Copier template (`_angee.kind: project`) that scaffolds
a project repository: `manage.py`, `settings.yaml`, the consumer-addon namespace,
and the web package. It owns the root. A project is *run* by a **stack**, which is
a separate concern: a developer overlays a dev stack into a gitignored `.angee/`
with `angee init --dev`, and a self-contained local/staging/prod instance is the
project at its own root with a thin stack overlay chained onto it — the stack keeps
its own `.copier-answers.stack.yml` so the project's canonical `.copier-answers.yml`
stays the project's, and the framework comes from the runtime image rather than a
cloned source. The two stack layouts live in the operator's
[Concepts](/operator/concepts#two-stack-layouts).

**Host** — the application runtime a stack runs. `angee-django` is the first and
default Host; a project *is* the Host's source. The operator is Host-agnostic — to
it, a project is just a git Source.

**Addon contract** — what an addon declares for the composer to consume (source
models, native Strawberry GraphQL classes, routes, slots, resources). Contracts
are the seams between addons.

**Seams** — the named extension points and boundaries the framework owns. The
framework owns the seams; addons own the concerns. Extension is mechanical: named
hooks, explicit owners, deterministic order, fail-fast on collisions.

## Backend

**Source model** — an abstract Django model defined in an addon. Source models are
abstract; the composer emits concrete apps from them. You edit source models, not
the emitted output.

**Concrete app** — a runtime Django app emitted by the composer from source models.
Generated output — change the source, not the artifact.

**`runtime/`** — the directory of generated backend output (concrete apps,
GraphQL SDL, codegen stubs, migrations). Output, not source.

**Model extension (same-row)** — an abstract source model with
`extends = "app.Model"`. The composer emits it as an additional base for the
target model, adding fields or behavior to the same database row.

**Model extension (materialized child)** — a concrete Django child model that
specializes a parent model when a row is exactly one concrete kind of that
parent. It shares the parent identity and materializes kind-specific fields in
its own table. In conversation, "extend a model" can mean either this child-row
reading or the same-row `extends` reading above; choose by row semantics.

**GraphQL type extension** — a Strawberry extension contribution that adds fields
to an existing GraphQL type. It is not a model `extends`; it extends the API
projection, not the Django model class.

**Child model** — the materialized-child reading of model extension. The parent
owns common identity and lifecycle; the child owns kind-specific fields,
behavior, tabs, and actions.

**Backend class** — an `ImplClassField` value on a concrete owner model that
selects an interchangeable strategy/client/backend while the row's persisted
shape stays the same.

**`Meta`** — Django's model options class. Keep Angee facts out of `Meta` unless
the owning library explicitly supports them, such as `rebac_resource_type`.

**REBAC** — Relationship-Based Access Control (via `django-zed-rebac`).
Authorization is structural: reads scope through the model manager, writes check
the instance. Addons keep the owning `permissions.zed` contract adjacent to the
addon (discovered by convention); `django-zed-rebac` owns sync.

**Principal** — an identity that acts. The word means different things at
different layers, deliberately. At the **database layer there is exactly one
principal record**: a row in the swappable `AUTH_USER_MODEL` table — person or
service alike — and every fact that answers "who" (audit stamps, history rows,
revision authors) is an FK to that table. At the **authorization layer** a
principal is a REBAC subject and keeps its species (`auth/user` for people,
`agents/agent` for agents); authorization never collapses an agent into its
user row. The two layers are *linked* (`actor_user_id` + the resolver
registry), never merged. So `user = service account = actor = principal` is
true **only at the database layer**, where all four words name the same row.

**Actor** — the REBAC subject bound to the current operation (the
`django-zed-rebac` actor context). Species-preserving: an agent acts as
`agents/agent:<sqid>`, a person as `auth/user:<id>`. When attribution needs a
database FK, the actor resolves to its user row through `actor_user_id`; it is
never *replaced* by it for permission evaluation.

**User (row)** — the database-layer principal record. Not synonymous with "a
human" or "a login": `kind` distinguishes `person` from `service`, and only
person rows authenticate. Real-world faces link to it one way, one shape:
`parties.Person.user` for humans, `agents.Agent.user` for agents.

**Service account** — a `kind=service` user row: the database-layer principal
of an agent or automation. Non-login (unusable password, excluded from OIDC
linking and user pickers); its lifecycle is owned by the thing it represents
(the agents manager creates, renames, and deactivates it with its `Agent`).

**Resource file** — tabular data owned by an addon and imported idempotently by
tier (`master`, `install`, `demo`). Addons list resource files in their
`addon.toml` `[resources]` manifest.

**GraphQL data resource** — a list/detail/mutation metadata contract emitted from
a GraphQL schema contribution for the frontend data-view layer. It is a UI/API
surface, not an import file.

**REBAC resource** — an authorization object (`ObjectRef`) in the
`django-zed-rebac` schema. It names what an actor can read/write; it is separate
from resource files and GraphQL data resources.

**Symbolic model reference** — referring to a model by symbol/string across addon
boundaries instead of importing it, to avoid import cycles.

**GraphQL contribution** — native Strawberry types exported through the
`schemas` mapping in an addon's conventional `schema.py`. Each named schema
contributes to fixed buckets, and Angee builds one Strawberry `Schema` per name.

## Frontend

**`defineAddon`** — the frontend entry point an addon uses to contribute routes,
views, slots, and other UI to the composition.

**`createApp`** — the frontend entry point the host uses to compose addons into the
running app.

**Slot** — an additive extension point in the component tree. Contribute to a slot
before copying or forking a component.

**Token** — a semantic styling value (Tailwind). Theme by overriding tokens rather
than passing color props or one-off variants.

**Rendered binding** — the single rendered (styled) Angee binding over Refine
state, owned by `@angee/ui`.
