# Glossary

Shared vocabulary for working in the Django / React Runtime. Terms are defined once here;
other docs link to this file instead of redefining them. This is a living
document — add a term when it first needs explaining, and keep each definition to
the smallest accurate statement.

## Composition

**Angee** — a thin composition framework that binds proven libraries into one
deterministic product surface. It owns the seams, not the concerns.

**Component** — a Python distribution authored by `pyproject.toml`. For the
current django-compose contract, each participating component provides one Django
app; models, a custom AppConfig and a provisioning hook are optional. Installed
identity, version and requirements come from native distribution metadata.

**Addon** — an Angee component advertised by an `angee.addons` entry point.
Its package declares requirements and capability facts; its Python
and web modules implement those capabilities. Installation makes it available;
project roots and their required apps determine whether it is enabled.
**Addon** is also the user-facing name;
**App** is reserved for a domain root or an included, non-flattened app in the
product rail.

**Sub-app** — an app included beneath another app without flattening. The rail
shows root apps and their sub-apps at most two levels; each app's own menu items
belong in the top bar.

**Framework addon** — a reusable capability shipped with Angee under `src/`.
Foundation primitives belong to `angee.base`, HTTP serving policy to
`angee.server`, and generic composition to the independent `django-compose`
library. There is no separate Angee core package.

**Foundation addon (`angee.base`)** — the reusable model foundation: models,
mixins, fields, managers, querysets, identity, validation, permission integration,
deployment settings and its permission-synchronization provision hook. Its
persisted Django app label is `base`.

**Consumer addon** — an addon written by a product team for a specific project,
built on top of the framework and its reusable addons.

**Composer** — the independent `django-compose` library. It resolves app
selection, emits concrete models, and publishes and verifies artifacts supplied
by capability integrations. It also owns explicit database preparation and
readiness for the verified composition. Permission, resource and web semantics
stay with those integrations. See [The Composer](composer.md).

**Project settings** — the authored Python module selected by Django's
`DJANGO_SETTINGS_MODULE`, with optional terse `settings.yaml` loaded through
`angee.base.conf.configure`. Projects declare root `INSTALLED_APPS` and overrides;
the loader delegates app selection and contribution merging to django-compose.
Settings are not generated.

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

**Addon contract** — the addon facts declared in `pyproject.toml`, parsed by
native metadata readers and consumed by their capability owners. Fields, executable
behavior and native library objects remain in their Python or web modules;
manifest references and conventional defaults connect those implementations.

**Seams** — the named extension points and boundaries the framework owns. The
framework owns the seams; addons own the concerns. Extension is mechanical: named
hooks, explicit owners, deterministic order, fail-fast on collisions.

## Backend

**Source model** — an abstract Django model declared in an addon. Native `Composable` class keywords (`concrete`, `extends`, `inherits`,
`demands`) select its composition role; unmarked abstract
helpers are ordinary reusable bases. Edit the source model, not emitted output.

**Concrete app** — the generated concrete model module and migration package
for a source Django app. Its models register under the source app's existing
AppConfig identity. Change the source, not the generated artifact.

**`runtime/`** — the directory of generated backend output (concrete apps,
GraphQL SDL and web artifacts). Generated models/artifacts are replaceable;
tracked native migration packages beneath it are durable project history.

**Model extension (same-row)** — a narrow abstract donor with its own
`extends="app.Model"`. The donor class precedes the
target source in the generated model's bases, adding fields or behavior to the
same database row.

**Model extension (materialized child)** — a Django multi-table-inheritance
child emitted from a narrow abstract `Composable` source declaring
`inherits="app.Model"`. It shares the concrete parent's identity and stores
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

**Concrete kind** — a child model seen from its parent: one kind of row the
parent concept can be, sharing the parent's identity. Resource metadata lists a
parent's exposed kinds as `concrete_kinds`.

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
user-backed audit and history stamps refer to that table. Changeset attribution
also accepts non-user REBAC subjects.

**Actor** — the REBAC `SubjectRef` bound to the current operation. For an
`auth/user` subject its ID is the user's database primary key; `actor_user_id`
converts it to the FK's Python type and returns no user for other subject types.

**Changeset** — attribution for one logical write, owned by
[`changeset()`](../src/angee/provenance/context.py) and stored by the
[provenance addon](../src/angee/provenance/models.py).

**Action / activity** — action uses FHIR AuditEvent's C/U/D verbs; activity
classifies the logical work, including `pull`/`push` for sync, `import` for a
one-shot import and `seed` for seed loads. The
[`Changeset` enums](../src/angee/provenance/models.py) own that vocabulary.

**Write group** — an internal ephemeral identity for nested ORM dispatch. Its
deduplication ends with the dispatch; successive writes in one changeset remain
distinct history events.

**Statement** — one source's current stance on one aspect of one subject row
(Wikibase's statement: claim, references, rank). Each canonical model declaring
`statements = StatementRecords(aspects=…)` gets a typed `<Model>Statement` table
beside its history table, read through the subject like history rows and
written only by `statements.set()` and the status verbs
([owner](../src/angee/provenance/statements.py)).

**Aspect** — a declared field group of a canonical model, optionally with a
typed member (a Handle, a counterparty), carrying its source precedence. The aspect is the unit of resolution; a value field belongs
to exactly one aspect, and consumer donors contribute aspects additively.

**Survivorship** — how an aspect's asserted normal-rank statements resolve onto
the operational row through the model's source-priority ordering. Preferred
person statements precede normal statements; the winner is marked **applied**
and its values materialise on the row. Changing precedence re-derives that row.

**SourceKind** — the assertion-time classification of the asserting principal:
`PERSON`, `SYSTEM_OF_RECORD`, `DIRECTORY`, `NETWORK`, `IMPORT`, `SEED`,
`DOCUMENT`, `EXTRACTION`, `RULE`. Resolved through
`StatementSourceMixin.source_kind` and retained on the statement; precedence is
declared per aspect, never globally.

**Authoritative** — the per-stream knob (`SyncStream.authoritative`) that makes a
`SYSTEM_OF_RECORD` source the system of record for the aspects naming its kind,
ahead of people's normal-rank statements; unset, the stream is a system of
reference below people (DAMA). A person's confirm or dismiss stays on top.

**Rank / snak / status** — a statement's three facts (Wikibase): rank
`preferred`/`normal` (a PERSON writes `preferred`; seeded install data may hold a standing decision), snak
`value`/`some_value`/`no_value`, and status `asserted`/`withdrawn`/
`quarantined`/`deprecated` — the source's current stance. Canonical candidates
are asserted; `confirm()` writes a preferred value, `dismiss()` a preferred
no-value that clears the aspect's values and keeps a removal durable.

**Applied** — the statement survivorship chose for its aspect and member: one per
current key, the row `provenance(field)` returns, flagged on the statement so a
trace is one indexed read.

**Withdraw / quarantine** — `withdraw` is a source retracting its stance
(`status=withdrawn`; the row and its history stay); `quarantined` is a stance
held back from resolution (PREMIS). Sync's quarantine of a failed inbound
application is a **SyncFailure**, not a statement status.

**SyncFailure** — one failed inbound application or refused write-back, retained
on its `RecordLink` (or on the `SyncStream` when no link exists) with kind, code,
source hash, mapping version, details, attempts, retry and resolution. The one
noun for that state; "discrepancy" is retired.

**Rule / Seed** — provenance's persisted non-user principals: `Rule` (one row per
registered rule or matcher, kind RULE, created by the owning resource
declaration) and `Seed` (one row per seed load, kind SEED). They assert the
statements no person or integration made.

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
`pyproject.toml` `[tool.angee.resources]` manifest.

**GraphQL data resource** — a list/detail/mutation metadata contract emitted from
a GraphQL schema contribution for the frontend data-view layer. It is a UI/API
surface, not an import file.

**Record edge** — a polymorphic edge model (`RecordRefMixin`, such as
`storage.FileAttachment`) whose schema relations declare the record types it
may target. A data resource's `recordEdges` lists the edges its records can
carry; record surfaces for one edge apply only there.

**Merge** — folding one or more *merged records* into a *survivor* of the same
model (`MergeableMixin.merge`, the shared `merge_records` verb). Each
*reference* to a merged record, meaning a foreign key or a record edge pointing
at it, follows its merge policy: move to the survivor, keep pointing at the
merged record, or block the merge. The merged record is then retired
(`retire_merged`): kept as a redirect to the survivor (`merged_into`, trashed on
a trash owner) when the model, its trash owner or the references naming it
retain it, otherwise deleted. A resource's `mergeable` says the verb serves it.
An actor merges through `merge`; system work folds through `fold`, inside the
caller's changeset.

**Canonical record** — the operational survivor a redirect resolves to
(`MergeableMixin.canonical()`, `objects.canonical()`): the one row readers,
writers and counts address once its duplicates are merged. A merged record stays
as evidence and reads as *merged into* its canonical record. A queryset's
`canonical()` keeps a retained redirect whose survivor that queryset does not
reach (an actor's read, a channel filter), so a reader who can see only the copy
still sees the record.

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

**Held reply** — an outbound draft awaiting operator approval or its scheduled
release. Discarded and failed replies do not exclude another answer. See
[posts reply policy](../src/angee/posts/README.md).

**Reply hold** — a feed's operator-controlled release policy: approval-only,
release after a duration, or queue immediately. See the
[posts owner](../src/angee/posts/models.py).

**Feed stream** — an independently ordered public-feed partition driven by
integrate's shared stream engine; activity and history classify each record
against the feed's live horizon. See [public feeds](../src/angee/posts/README.md).

**Delivery settlement** — messaging's reflection of provider acceptance or refusal
into the outbound message's status, provider identity and retry schedule. See
[the delivery owner](../src/angee/messaging/delivery.py).

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
`angee.provenance.evidence`'s standing-read check. Decisions, run inputs and
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
The Handle's controller records the **control** fact (who may act through it), while
**PartyStatement** records the **identity** fact (`aspect="handle"`, typed Handle
member); neither fact implies the other. A **suggestion** is an asserted
normal statement below 0.5 confidence: it awaits review and never decides which
Party the Handle reaches. A person's preferred value confirms membership; a
preferred no-value dismisses it durably. Applied directory/person statements
and confirmations publish to a card. Other sources remain inferences even at
full confidence. Reads follow Party authorization, with separate Handle and
evidence gates.

**Circle** — one user's private, Dunbar-sized organizing tree. It never gates
visibility.

**Space** — a shared, governed roster (`spaces.Group`, `spaces/group`). Its
roster roles participate in access control. It is distinct from an IAM group,
which is a set of authorization principals.

**Relationship** — the single typed Party-to-Party factual edge, including
employment as one relationship kind rather than a separate identity model.
It remains canonical for tracked or free-text, time-bounded counterparties;
its asserted values live in its own statement table. Address is likewise a
canonical, referencable subject with independently retained source assertions.

**same_as** — a symmetric identity statement on an ordered pair of records of
one model (Party, and messaging's Thread and Message). Preferred no-value keeps
both directions separate and refuses their merge. For a Party a preferred value
awaits a merge action whose input chooses the survivor; survivorship never
merges rows. For a channel copy of a message or thread the applied statement
itself folds the newer copy into the older as a retained redirect.
The pointing model's **merge_contract** owns statement union and retained
supersession through the existing reference dispatcher.

**Revert** — a Changeset restoring each row's pre-boundary state and re-running
survivorship after refusing later touches. Created subjects use trash where
available; their statements withdraw rather than disappear. A **document** is
the deterministic changeset/history export consumed by a future archive sink.

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
