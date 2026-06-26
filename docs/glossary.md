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
entrypoints, and generated runtime output.

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

**Model extension** — an abstract source model with `extends = "app.Model"`.
The composer emits it as an additional base for the target model.

**Child model** — a concrete Django model that specializes a parent model when a
row is exactly one concrete kind of that parent. The parent owns common identity
and lifecycle; the child owns kind-specific fields, behavior, tabs, and actions.

**Backend class** — an `ImplClassField` value on a concrete owner model that
selects an interchangeable strategy/client/backend while the row's persisted
shape stays the same.

**`Meta`** — Django's model options class. Keep Angee facts out of `Meta` unless
the owning library explicitly supports them, such as `rebac_resource_type`.

**REBAC** — Relationship-Based Access Control (via `django-zed-rebac`).
Authorization is structural: reads scope through the model manager, writes check
the instance. Addons point their AppConfig `permissions` attribute at the owning
`permissions.zed` contract; `django-zed-rebac` owns sync.

**Resource** — tabular data owned by an addon and imported idempotently by tier
(`master`, `install`, `demo`). Addons list resource files in their
`AppConfig.resources` manifest.

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
state. During the package split this code lives in `@angee/base`; the target
owner is `@angee/ui`.
