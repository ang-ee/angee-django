# Glossary

Shared vocabulary for working in the Django / React Runtime. Terms are defined once here;
other docs link to this file instead of redefining them. This is a living
document — add a term when it first needs explaining, and keep each definition to
the smallest accurate statement.

## Composition

**Angee** — a thin composition framework that binds proven libraries into one
deterministic product surface. It owns the seams, not the concerns.

**Addon** — the unit of capability. An addon declares a contract (source models,
operations, routes, containers, resources) that the composer assembles into a project.
Everything that gives a product a capability, including its API protocol, is an
addon. The framework core is not. **Addon** is also the user-facing name;
**App** is reserved for a domain root or an included, non-flattened app in the
product rail.

**Sub-app** — an app included beneath another app without flattening. The rail
shows root apps and their sub-apps at most two levels; each app's own menu items
belong in the top bar.

**Framework core** — Angee's composition language and loom: the data contract,
composer, model toolkit, serving seams, and jobs seam. It is the `django-angee`
wheel, not an addon.

**Framework addon** — a reusable product capability that is part of Angee itself
and builds on the framework core. Framework addons live under `addons/`.

**Base addon** — a synonym for framework addon, not another architectural level.

**Consumer addon** — an addon written by a product team for a specific project,
built on top of the framework and base addons.

**Composer** — the framework machinery that resolves addon declarations and
emits concrete model modules, permission extensions and other `runtime/`
artifacts. It selects class definitions before Django imports and registers them;
runtime model classes are not monkey-patched. See [The Composer](composer.md).

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
a separate concern. Both the default development stack and a self-contained
instance chain the project at the stack root; the stack keeps its own
`.copier-answers.stack.yml` so the project's canonical `.copier-answers.yml`
stays the project's. The two stack layouts live in the operator's
[Concepts](https://docs.angee.ai/operator/concepts#two-stack-layouts).

**Host** — the application runtime a stack runs. `angee-django` is the first and
default Host; a project *is* the Host's source. The operator is Host-agnostic — to
it, a project is just a git Source.

**Addon contract** — the addon facts declared in `addon.toml`, parsed by
hatch-angee and consumed by composition and capability owners. Fields, executable
behavior and native library objects remain in their Python or web modules;
manifest references and conventional defaults connect those implementations.

**Seams** — the named extension points and boundaries the framework owns. The
framework owns the seams; addons own the concerns. Extension is mechanical: named
hooks, explicit owners, deterministic order, fail-fast on collisions.

## Backend

**Source model** — an abstract Django model declared in an addon. Its own
`runtime` and `extends` markers select its composition role; unmarked abstract
helpers are ordinary reusable bases. Edit the source model, not emitted output.

**Concrete app** — the generated concrete model module and migration package
for a source Django app. Its models register under the source app's existing
AppConfig identity. Change the source, not the generated artifact.

**`runtime/`** — the directory of generated backend output (concrete apps,
GraphQL SDL, codegen stubs, migrations). Output, not source.

**Model extension (same-row)** — a narrow abstract donor with its own
`extends = "app.Model"` and no own `runtime = True`. The donor class precedes the
target source in the generated model's bases, adding fields or behavior to the
same database row.

**Model extension (materialized child)** — a Django multi-table-inheritance
child emitted from a narrow abstract source declaring both `runtime = True` and
`extends = "app.Model"`. It shares the concrete parent's identity and stores
kind-specific fields in its own table. Its source precedes the concrete parent
in the generated bases, so child behavior overrides through ordinary Python MRO.
In conversation, "extend a model" can mean either this child-row reading or the
same-row `extends` reading above; choose by row semantics.

**GraphQL type extension** — a Strawberry extension contribution that adds fields
to an existing GraphQL type. It is not a model `extends`; it extends the API
projection, not the Django model class.

**Child model** — the materialized-child reading of model extension. The parent
owns common identity and lifecycle; the child owns kind-specific fields,
behavior, tabs, and actions.

**Backend class** — an `ImplClassField` value on a concrete owner model that
selects an interchangeable strategy/client/backend while the row's persisted
shape stays the same. Its base class names the implementation registry setting.

**`Meta`** — Django's model options class. Keep Angee facts out of `Meta` unless
the owning library explicitly supports them, such as `rebac_resource_type`.

**REBAC** — Relationship-Based Access Control (via `django-zed-rebac`).
Authorization is structural: reads scope through the model manager, writes check
the instance. Addons keep the owning `permissions.zed` contract adjacent to the
addon (discovered by convention); `django-zed-rebac` owns sync.

**Principal** — an identity that acts: a row in the swappable
`AUTH_USER_MODEL` table and its
`auth/user` REBAC subject. The row may represent a person or a service. Every
fact that answers "who" (audit stamps, history rows, revision authors) is an FK
to that table.

**Actor** — the `auth/user` REBAC subject bound to the current operation (the
`django-zed-rebac` actor context). Its subject ID is the user's database primary
key; `actor_user_id` converts that canonical value to the FK's Python type.

**User (row)** — the database-layer principal record. Not synonymous with "a
human" or "a login": `kind` distinguishes `person` from `service`, and only
person rows authenticate. Real-world faces link to it one way, one shape:
`parties.Person.user` for humans, `agents.Agent.user` for agents.

**Service account** — a `kind=service` user row: the database-layer principal
of an agent or automation. Non-login (unusable password, excluded from OIDC
linking and human-only member pickers). Access pickers include readable service
users; agents and workflows link one service user each, with their row lifecycle
calling IAM's shared sync and deactivation helper.

**Workflow principal** — the workflow's linked service user. Trigger source
grants are direct REBAC tuples for this user. Admission, source and domain checks,
and triggered runs act as it; the user who enables a trigger only authorizes
the source grants at enable time. A human-published version may run as this
principal only while its publisher can delegate every enabled trigger grant;
the same check governs child runs. System-installed versions are trusted.

**Trigger source grant** — a source-declared, listable and revocable REBAC tuple
assigned to the workflow principal when its trigger is enabled. A
`record_changed` model declares its grant scope when it opts in.

**Trigger enable preview** — the prospective source grants and the users and
groups with workflow monitoring access to runs the trigger will start. Only a
user eligible to enable the trigger may see it.

**Agent** — an autonomous capability represented by an `agents.Agent` and its
linked service-account user row. The agent acts as that ordinary `auth/user`
subject, and its reach is exactly the grants assigned to the service user.

**Group** — an IAM-owned named set of principals (`auth/group`). Membership
lives in REBAC tuples and includes people and service users. The group's
canonical subject is its `#member` set. Its public display ID is separate from
the stable database primary key used by existing authorization references.

**Role** — a schema-declared reach anchor, such as `storage/role:storage_admin`.
An addon declares the permission arms that give the role meaning. Membership
is runtime data; creating a new name cannot create a permission arm. Relations
store the plain role subject, optionally constrained to a fixed role ID, and
permission arrows compute its effective members.

**Relation** — a named relationship on a resource, such as `reader` or
`editor`. The resource schema owns its allowed subject types; permissions own
computed reach. A relation-backed userset such as `auth/group#member` may be a
stored subject, while a computed permission such as `#effective_member` may not.

**Binding** — a relationship tuple granting a principal or a group's member
set a resource relation or role membership. A dynamic composite role is an IAM
group with bindings; it requires no additional role model or schema edits.

**Kind** — the IAM user's `person` or `service` value, exposed live through
`iam/kind` membership. Human-only authority requires an active person; kind
does not change the principal's `auth/user` identity.

**Resource file** — tabular data owned by an addon and imported idempotently by
tier (`master`, `install`, `demo`). Addons list resource files in their
`addon.toml` `[resources]` manifest.

**GraphQL data resource** — a list/detail/mutation metadata contract emitted from
a GraphQL schema contribution for the frontend data-view layer. It is a UI/API
surface, not an import file.

**Resource query** — the executable query contract of a data resource. The
backend finalizes `DataResourceQuery` against the composed schema; frontend
`ResourceQuery` resolves filters, selections, ordering and group axes from it.
Explicit local row declarations use the same contract and semantics.

**Group axis** — one query dimension with a stable bucket identity, optional
display label, required row selections and optional server grouping and drill
projections. Labels describe buckets; identities distinguish them.

**REBAC resource** — an authorization object (`ObjectRef`) in the
`django-zed-rebac` schema. It names what an actor can read/write; it is separate
from resource files and GraphQL data resources. A model-backed resource or
subject uses its database primary key as the authorization ID. A sqid is that
key's public representation, encoded and decoded at API boundaries by the
model's public-ID field. Tableless anchors, such as roles, keep named IDs.

**Symbolic model reference** — referring to a model by symbol/string across addon
boundaries instead of importing it, to avoid import cycles.

**GraphQL contribution** — native Strawberry types exported through the
`schemas` mapping in an addon's conventional `schema.py`. Each named schema
contributes to fixed buckets, and Angee builds one Strawberry `Schema` per name.

## Workflows

**Workflow version** — an immutable published definition, including the
normalized contracts of any child workflows it awaits. A run pins one version.

**Run origin and cause** — the origin describes why a run started; exactly one
protected link identifies its parent step, prior run, or trigger event when one
caused it. A manual run has no cause. A cited cause stays retained until its
dependent effects are pruned.

**Node** — a keyed graph declaration naming a step, its bindings and outgoing
edges in a `Definition` document.

**Step** — the Python class a node runs, with typed input, output and config.

**Step run** — one execution of a node within a workflow run. The persisted
`StepRun` is named `step_run` in code; the reverse relation is `run.step_runs`.

**Attempt** — one try of a step run, recorded by `StepAttempt` from claim to
settlement. A retry creates another attempt for the same step run.

**Settlement** — what a step returns: `Done`, `Wait`, `NextPage`, `Ask` or `Fail`. It describes the
attempt's completion or continuation, and the transition owner persists it.
Runner-only retry and diagnostic facts belong to the attempt record.

**Result** — what a workflow run reports, selected and projected from its
declared producer bindings by `Definition`.

**Decision step** — one node that asks one question, holds for its verdict,
and applies the chosen alternatives as the run actor. `StepRun.decision` owns
the link. An application error holds the step for retry.

**Step record** — one `StepRecord` link retaining the record and the operation
read, created, changed, deleted or called. Admission inputs use the same relation
with no step. Readers see record identities through their current read scope.

**Derived-from evidence** — a frozen fact or source link admitted through
`angee.base.evidence`'s standing-read check. Decisions, run inputs and
extraction retain their own edges to this shared source identity.

## Decisions

**Decision** — one immutable question concerning records, with a proposal of
alternatives and a nullable verdict. The asker consumes the answer.

**Kind** — the question content key used for its presentation.

**Proposal** — alternatives with labels, per-record actions and continuation
outcomes; `multiple` permits several choices.

**Verdict** — chosen alternative keys, answerer and answer time. No verdict means
open; an empty verdict means the asking owner withdrew the question.

**Attention** — a readable record has at least one open decision, whoever may
answer. It is derived and pays no query cost when a list does not request it.

**Inbox** — the person's readable decisions, filtered by assignment or requester
to distinguish questions they can answer from questions they issued.

## Extraction

**Extraction** — an independent evidence domain that retains source-grounded
documents, facts and revisions against an explicit file or message target.
`workflows_extraction` is its workflow adapter; it does not own the evidence.

## Relationship Management

**Party** — the universal supertype for a person, organization, or other actor
that participates in identity, communication, and relationship facts.

**Handle** — a normalized contact point such as an email address or account.
The Handle's owner records the **control** fact (who may act through it), while
**PartyHandle** records the **identity** fact (which Party it reaches); neither
fact implies the other.

**Circle** — one user's private, Dunbar-sized organizing tree. It never gates
visibility.

**Space** — a shared, governed roster (`spaces.Group`, `spaces/group`). Its
roster roles participate in access control. It is distinct from an IAM group,
which is a set of authorization principals.

**Relationship** — the single typed Party-to-Party factual edge, including
employment as one relationship kind rather than a separate identity model.

**Tie** — a derived Party-to-Party interaction edge. **Cadence** is the human,
per-user stay-in-touch intent; recomputable interaction evidence and personal
intent remain separate facts.

**posts** — the public-post and engagement overlay on `messaging`. A Feed is a
Channel child, so posts use the same channel-scoped message identity and access.

## Frontend

**Files / `storage`** — **Files** is the user-facing product name; `storage` is
the technical addon, route, model, and i18n namespace.

**AI / `Inference*`** — **AI** is the user-facing product name; `Inference*`
names the technical models, resources, routes, and APIs behind it.

**Permissions / `iam`** — **Permissions** is the user-facing product name;
`iam` is the technical addon, route, model, and i18n namespace.

**`defineAddon`** — the frontend entry point an addon uses to contribute routes,
views, container children, and other UI to the composition.

**`createApp`** — the frontend entry point the host uses to compose addons into the
running app.

**App** — a top-level menu root, with the `home`, `brand` and `theme` it shows
when selected. A **named app** is a deployment's rail of roots
(`ANGEE_UI.shell.apps`).

**Selection** — the app the console shows: `?app=` or the hostname's
`ANGEE_UI.shell.hosts` entry picks a root or a named app, whose **rail** of
roots the rail, palette and menus follow; nothing selected shows every root.
It shapes navigation and identity, never which routes open.

**Aggregator** — an addon that places other addons' apps under its own menu root
(`include`), like the `angee.pm` suite. A **flattened** include shows the app's
items as the aggregator's own while the app keeps its routes and words.

**Mount** — a menu node that borrows another addon's page (`mount: "<route>"`):
an alias route named after the node, under the node's app, reusing the page.
Unlike an include, which absorbs an app and takes it off the rail, the source
app keeps its page.

**Layer** — one composed addon manifest, ordered by its addon dependencies; the
deployment layer comes last. Menus and containers resolve layer by layer.

**Container** — a named, ordered list on a node that the node's owner renders,
addressed `node#name` (`form#sections`, `projects.Task#actions`,
`record#aside`). Its entries are **children**, one addressed
`node#name/id`. An addon declares children in its own namespace and alters or
narrows its dependencies' children. Add a child before copying or forking a
component.

**Token** — a semantic styling value (Tailwind). Theme by overriding tokens rather
than passing color props or one-off variants.

**Theme** — a named visual implementation contributed by an installed addon.
It may provide semantic token layers, bounded options, scoped CSS and bundled
assets. Its stable ID is stored as an appearance preference.

**Theme addon** — the ordinary Angee addon that distributes one or more themes.
Installing it adds choices to the composed catalogue; installation does not
activate a theme.

**Appearance** — the effective presentation and the user or host settings that
select it: a theme, a color-scheme preference and that theme's supported options.

**Color scheme** — the light or dark rendering of a theme. A preference may be
`light`, `dark` or `system`; the resolved DOM state is always `light` or `dark`.

**Template** — a reusable scaffold or content/layout structure, such as a
project template or dashboard template. A visual design selected at runtime is
a theme rather than a template.

**Rendered binding** — the single rendered (styled) Angee binding over Refine
state, owned by `@angee/ui`.

**Settings place** — the one synthetic destination in every console that groups
every menu node declared with `group:"platform"`, at any depth. Its rail entry
sits below the scrolling list; the chooser exposes one Settings entry. Inside it, the expanded rail shows
the contributing platform roots and a back header; their menu items live in the
top bar.
