# AGENTS.md

Angee is a thin composition framework for Django + React applications. It binds
boring, proven libraries into one deterministic product surface. Before adding,
replacing, or hand-rolling a capability, check the opinionated stack in
`docs/stack.md`; it is the single source of truth for which library owns what.
The dependency manifests lock the install shape: `pyproject.toml` + `uv.lock`
for Python here, and the stack host's `package.json` + `pnpm-workspace.yaml` +
`pnpm-lock.yaml` for TypeScript (the JS packages live in sibling repos).

The framework owns the seams, not the concerns. Product logic belongs in addons.
The composer turns addon contracts and project settings into a runnable project.
A project declares the root apps it composes through Django `INSTALLED_APPS`.

## Repository Role

This repository holds the Angee framework core. The framework core is an addon
like any other; the base addons (the sibling `angee-base` repo) build on it, and
consumer addons (a product team's own code) build on those. See
`docs/glossary.md` for these terms.

Because everything is an addon, the first question for any change is *what level
does it belong to?*

- **Framework / base addon** — a capability that belongs to Angee and is
  inherited by every project downstream. A change here is copied into every
  consumer, so hold it to the highest bar.
- **Consumer addon** — product logic for a specific project, built on the
  framework.

Put each change at the level that owns the concern. Never solve at the consumer
level what the framework should own, and never push product specifics down into
the framework. Keeping each fact at its owning level is what keeps the whole
stack DRY.

## Repository Layout

A map by role, not a file inventory — each addon's `AppConfig` and module
docstrings own the current contract, and this points to the owner. Everything is
an addon: the framework core at `angee/` is the one real Python package
(`django-angee`); every other piece of the platform lives in a sibling source
repo, materialized side by side as workspace slots by a stack's `src` workspace.
The `angee.*` namespace spans the core and the base addons across repos without
changing any import.

```text
.                           # a workspace slot: <stack>/workspaces/<ws>/angee-django
├── angee/                  # `django-angee` — framework core + composer (PEP 420 namespace, no __init__.py)
│   ├── base/               # framework core: the model toolkit (abstract models, mixins, fields, managers)
│   ├── graphql/            # the GraphQL runtime — schema buckets, auto-CRUD, subscriptions, SDL (`manage.py schema`)
│   ├── compose/            # the composer — emits the concrete runtime (`manage.py angee build`)
│   └── tasks/              # the Celery seam (broker wiring, beat, queue routing)
├── docs/                   # intent docs — glossary, stack, guidelines, and `docs/howto/`
├── tests/                  # framework tests (composition, GraphQL, IAM, CRUD, …)
├── .agents/                # shared agent methodology — reviewer agents, commands, skills, workflows (`.agents/README.md`; public)
├── .work/                  # private agent work-state — plans, notes, handovers (gitignored symlink to a separate private repo; never mirrored)
├── README.md               # human entry point; `AGENTS.md` is the agent/contributor entry point
└── pyproject.toml, uv.lock # the Python package + locked graph

# Sibling workspace slots (their own repos, side by side):
../angee-react/             # `@angee/app`, `ui`, `refine`, `metadata` + the storybook and e2e workshops
../angee-base/              # the base addons — folders with addon.toml + co-located `web/` fragments
../angee-messaging-bridges/ # the opt-in personal-messaging bridge addons
../angee-examples/          # showcase consumer addons (`example.notes`) + the reference e2e suite
../angee-templates/         # the Copier templates — project / stack / workspace / service kinds
../angee-operator/          # the `angee` CLI / operator daemon (Go)
```

You edit **source models** in addons; the composer emits the concrete apps and
the `runtime/` tree. Generated `runtime/` is output — change the source, not the
artifact (see `docs/glossary.md`).

## Constitution

**Find the owner — the first question for every change.** Every fact has one
owner: an existing Angee pattern, an underlying framework or library, a file, or
a class. Ask that owner; never re-derive, re-decode, or re-decide from the
outside what it already knows. If the owner should answer but cannot, add the
method there instead of writing a helper that reaches in. If no Angee pattern
exists, follow the underlying framework's pattern. If the framework leaves
multiple plausible patterns, escalate to the human architect to set the pattern.
Code establishes patterns and docs reference them; a mismatch between code and
docs is a bug that requires reconciliation.

Dependencies point toward stable owners and policy, never outward toward details.
Framework and base addons must not depend on consumer addons; serving/runtime code
must not import the build-time composer; addon dependencies stay one-way; UI,
GraphQL, CLI, filesystem, vendor SDK, and generated-runtime details translate to
owners instead of deciding rules themselves.

The smell that means stop: a function that takes an object and inspects it to
decide something. This law has three faces:

- **Delegate to the library that owns the concern.** If `docs/stack.md` says a
  library owns it, wire it; do not rebuild it. Owner = a library.
- **Keep one source of truth per fact.** Move knowledge to the owning file or
  level instead of repeating it. Owner = a file or repository level.
- **Put behavior on the object that owns the data.** Prefer methods and
  properties on the owning class over loose helpers that decode its shape from
  outside; a function that switches on a value's type wants polymorphism. Owner =
  a class.

Before decomposing code, make an owner map. Classify each fact by the thing that
would answer it in the underlying framework: persisted facts live beside the
field or record that stores them, collection behavior lives on the collection
abstraction, instance behavior lives on the instance, declaration facts live on
the declaring object, and commands or routes stay thin dispatchers. If a helper
mostly forwards to, mutates, or interprets one owner, move the behavior to that
owner. If the move creates more ceremony than clarity, stop and choose the
smaller native framework shape.

## Architecture Gate

Before editing source for a structural change, refactor, new view, addon,
integration, model, or shared primitive, make the architecture check explicit in
your working note, plan, or user update. The goal is not ceremony; it is to stop
local patches from bypassing the owner that should make the whole platform
cleaner.

- **Owner map:** name the owner of each fact you are about to encode. Owners are
  Django, React, a locked dependency in `docs/stack.md`, an Angee primitive, an
  addon `AppConfig`, a model/manager/queryset, a schema contract, or a specific
  class. If no owner exists, create or extend the smallest owner at the right
  level before adding callers.
- **Sibling inventory:** search for the same page type, model pattern, schema
  pattern, or integration pattern in at least two nearby addons/packages. If the
  same shape appears twice, fix the shared owner or document why the shapes have
  different intent.
- **Dependency check:** consult `docs/stack.md` before hand-rolling behavior. If
  a locked library owns the concern, use its native shape and keep Angee glue
  thin. Add a dependency only by updating the stack row and manifest together.
- **Thin caller check:** entrypoints, routes, commands, resolvers, and page
  components should declare intent and dispatch. They should not accumulate
  business rules, persistence rules, data-view mechanics, or framework glue that
  belongs to an owner.
- **Deletion check:** a DRY/refactor change should make some existing code
  unnecessary. If lines increase, explain what owner is being introduced and what
  follow-up deletion it unlocks; otherwise stop and find the smaller native
  shape.
- **Naming check:** use one name for one concept across files, classes, routes,
  GraphQL fields, menu labels, settings, tests, and docs. A synonym is a design
  decision, not a convenience.

- Docs teach principles and point to owners; code states the concrete contract.
  Do not maintain a parallel code inventory in prose. Public classes, methods,
  fields, and settings helpers explain their current API with docstrings.
- Less is more. Better code is the documentation and the example.
- Compose at build time. Do not monkey-patch, register at runtime, or edit
  generated output.
- Prefer deletion to abstraction. Add an abstraction only when it removes real
  duplication.
- Make extension mechanical: named hooks, explicit owners, deterministic order,
  fail-fast collisions.
- Cross boundaries only through declared Angee contracts: `AppConfig`
  attributes, schema buckets, model `extends`, input/type extensions,
  settings/autoconfig, `ImplClassField` registries, slots, registered glyphs and
  forms, generated SDL, and dependency-native extension points. Do not cross a
  boundary by ad hoc import, object-shape probing, monkey-patching, or runtime
  registration.
- Domain knowledge never leaks into a framework-owned file. The framework owns
  the seam; each addon owns its own vocabulary and contributes it additively
  through that seam — never by editing a framework/base-addon file to name a
  product concept. A REBAC `permissions.extends.zed` fragment is the named
  example: a consumer addon contributes its own role relation to an existing
  resource definition from the addon that owns the role vocabulary; see
  `tests/extcontrib` for the living fixture.
- **Compose, never re-implement, at the addon level.** An addon composes the
  framework's shared primitives (the data grid/list/group/board views, forms,
  detail/record views, navigation, glyphs, state surfaces); it never hand-rolls
  one. A hand-rolled copy is a bug — it drifts from the owner and silently drops
  the affordances it never reproduced (a hand-rolled grid loses grouping,
  group-collapse, the column show/hide chooser, selection, sort/filter, keyboard
  nav). When you reach for a local component, first prove no shared primitive owns
  it (`docs/stack.md`; the view/form/table/shell primitives). If a primitive is
  missing, insufficient, or has a gap for your case, fix or extend it **at its
  owning level** (a base addon or the framework core) so every addon inherits it,
  then compose it; the gap is the signal the change belongs in the framework, not
  a workaround in the addon. For React, apply the same owner test to state: URL
  facts live in the router/search owner, server facts in GraphQL/urql,
  collection facts in the data-view primitive, form facts in the form primitive,
  and only short-lived interaction state stays local. Routes and pages compose
  those owners and pass declarations/handlers; they do not mirror facts into a
  bespoke component tree or hand-roll a shared view.
- Verify before claiming done. Drift is a bug, whether it is code, docs, schema,
  generated output, or tests.

## DRY

DRY is a core coding principle (`docs/guidelines.md`); this section is how it
applies here. This is framework code: every impurity in the foundation is copied
into addons, projects, examples, tests, and future decisions. Keep the foundation
clean so the code people copy is the code we want them to write.

A fact, rule, or primitive lives once, at the level that owns it (see Repository
Role), and everything above reuses it. When the same idea appears twice, find the
owner and remove the copy. Extract a helper only when it makes the next change
smaller and clearer.

- Same rule in two places: choose the owner, delete the copy, link if needed.
- Same shape in three places: extract the smallest boring primitive.
- Same words in docs: keep the durable sentence where the contract lives.
- Same bug in generated files: fix the generator or source contract.
- Similar code with different intent: leave it separate.

## Mechanical Overrides

- Never quick-fix to make something run. This is non-negotiable. In this repo
  every change creates technical *investment* — a fix at the owning level that
  leaves the whole platform cleaner — never technical *debt*, a workaround in the
  consuming code that papers over the real defect. Cutting scope, dropping a
  feature, weakening or disabling a check, or working around a failure to get a
  build / schema / test / `angee dev` to pass is a bug, not a fix — find the owner
  and fix it at the right level, however much more work that is. A workaround that
  only makes it run hides the defect and does more harm than the failure it
  silenced. When a fix is genuinely out of scope, stop and surface it; do not
  paper over it.
- Before structural refactors, remove dead code first.
- Re-read a file before editing it, and read it again after.
- If a search looks too small, narrow and rerun it.
- Sort build-time iteration; never use wall-clock time, random ids, or
  filesystem order in emitted artifacts.
- A clean/reset command may delete only the configured generated runtime
  directory, and only after verifying Angee's generated sentinel in it; it must
  preserve `*/migrations/` unless it explicitly documents deleting migrations.
- Put scratch files, screenshots, and logs only in gitignored locations such as
  `.playwright-mcp/`, `test-results/`, or `playwright-report/`.
- Keep the shared agent methodology — reviewer agents, slash commands, skills,
  workflows — in `.agents/` (see `.agents/README.md`). Keep private work-state —
  plans, notes, handover prompts — in `.work/` (a gitignored symlink to a
  separate private repo, never mirrored), not in `/tmp` or other scratch.

## Run From The Root

Resolve the stack that owns lifecycle before running `angee init`, `angee ws`,
or another stack command. An existing current or ancestor `angee.yaml` owns
this checkout: this repository is normally a workspace slot
(`<stack>/workspaces/<ws>/angee-django`) of a framework-dev stack whose root
manifest declares the project host, the sources, and the workspaces. Never
initialize a stack under a source checkout. Load
`.agents/skills/angee-workspace/SKILL.md` for the canonical resolution flow; it
binds the result as `angee_root` and runs lifecycle commands as
`angee --root "$angee_root" ws ...`.

`angee dev` is the only supported way to bring the local stack up — do not start
Django, Vite, Daphne, workers, or watchers by hand. Run it against the resolved
stack root; the stack's project host composes this checkout editable (the
rendered pyproject resolves `django-angee` from this slot).

```sh
angee --root "$angee_root" dev
```

`angee dev` is for bringing the long-running stack up. To run a one-shot Django
management command, drive the stack host's `manage.py` through `uv` from the
STACK root, never by `cd`-ing into a slot. The composer is emit-only;
migrations, permission sync, resource data, and GraphQL SDL checks are separate
later steps (a fresh process loads the freshly emitted concrete models):

```sh
cd "$angee_root"
uv run manage.py angee build              # emit runtime sources
uv run manage.py makemigrations base notes
uv run manage.py migrate
uv run manage.py rebac sync               # permissions, after migrate
uv run manage.py resources load           # data, after migrate
uv run manage.py schema                   # write SDL
uv run manage.py schema --check           # SDL, after runtime load
```

For an isolated branch, load `.agents/skills/angee-workspace/SKILL.md` and
follow its **Create Workspace** workflow — the src template cuts every
framework repo as a sibling worktree slot on `workspace/<name>`.

A workspace is pinned to `workspace/<name>` — never `git checkout`/`switch`
inside it; make a new workspace for a different branch.

## Development Process

Every task follows the process and coding principles in `docs/guidelines.md`:
research before building, think in first principles, describe and discuss the
goal, build with the right primitives, and stop when the code grows instead of
getting smarter. Apply it first, then follow the language-specific rules below.

## Definition of Done

A change is done only when the code and the architecture are both checked.

- The architecture gate above has been satisfied for any structural work.
- The change is at the level that owns the concern: framework/core, base addon,
  consumer addon, project settings, generated artifact source, or dependency.
- New behavior composes shared primitives or locked dependencies. Any exception
  is narrow, named, and documented where future work will find it.
- DRY/refactor work reports the net shape: what was deleted, what callers became
  thinner, and why any line increase is temporary or earns its keep.
- Generated outputs are regenerated from source, never edited by hand.
- Names are normalized across code, tests, docs, menus/routes, GraphQL, settings,
  and files touched by the change.
- Relevant backend, frontend, schema, browser, or e2e checks from the
  language-specific guidelines have run, or the final handoff states exactly why
  they could not run.

## Guide Split

- The development process and coding principles live in `docs/guidelines.md`;
  follow them for all development work.
- Term definitions live in `docs/glossary.md`.
- The opinionated stack lives in `docs/stack.md`; manifests lock exact
  dependency setup.
- Backend rules live in `docs/backend/guidelines.md`.
- Frontend rules live in `docs/frontend/guidelines.md`.
- Root rules stay here. Do not duplicate language-specific guidance in this
  file.

## Where Knowledge Lives

Durable project knowledge is checked in, not held in any agent's private memory
— so the whole team and every future agent inherits it.

- **Durable knowledge — conventions, gotchas/pitfalls, and architecture
  decisions — goes into the checked-in docs.** When you learn something that will
  matter next time, extend the owning guideline (`docs/backend/guidelines.md`,
  `docs/frontend/guidelines.md`, `docs/guidelines.md`, or `docs/stack.md`) as a
  terse rule or a `Pitfalls` entry. Don't restate code contracts (field/API
  inventories) — those live beside the code (see "Let Code Carry Code Contracts"
  in `docs/guidelines.md`).
- **Shared agent methodology — reviewer agents, slash commands, skills, and
  workflows — lives in `.agents/`** (committed and public; see `.agents/README.md`).
- **Agent work-state goes into `.work/`:** design specs: `.work/plans/specs/`;
  plans: `.work/plans/`; notes: `.work/notes/`; handovers: `.work/handovers/`.
  `.work/` is a gitignored symlink to a separate, private work-state repo; it is
  never mirrored to the public repo, and nothing in it is published. Global
  skill defaults such as `docs/superpowers/**` are overridden and forbidden in
  this repository.
- **Work-state is history, not reference.** Specs, plans, and notes capture
  intent at the time of writing and go stale as the code moves. To know how the
  system works now, read the code; verify any claim a spec or plan makes against
  the code before acting on it (see "Research" in `docs/guidelines.md`).
- **Durable rules the team must inherit never live only in `.work/`.** A private
  note is invisible to teammates and to the next agent; capture the durable rule
  in the owning `docs/` guideline instead.
