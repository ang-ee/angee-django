# AGENTS.md

Angee is a thin composition framework for Django + React applications. It binds
boring, proven libraries into one deterministic product surface. Before adding,
replacing, or hand-rolling a capability, check the opinionated stack in
`docs/stack.md`; it is the single source of truth for which library owns what.
The dependency manifests lock the install shape: `pyproject.toml` + `uv.lock`
for Python, and `package.json` + `pnpm-workspace.yaml` + `pnpm-lock.yaml` for
TypeScript.

The framework owns the seams, not the concerns. Product logic belongs in addons.
The composer turns addon contracts and project settings into a runnable project.
A project declares the root apps it composes through Django `INSTALLED_APPS`.

## Repository Role

This repository holds the Angee framework itself together with the base addons
shipped with it. The framework core is an addon like any other; the base addons
build on it, and consumer addons (a product team's own code) build on those. See
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
an addon: the framework core lives at `angee/` and the base addons at
`addons/angee/` — two source roots sharing the one `angee.*` namespace, so the
directories mirror the eventual `django-angee` / `django-angee-addons` split
without changing any import. The example shows a host composing a consumer addon
on top.

```text
.
├── angee/                  # `django-angee` — framework core + composer (PEP 420 namespace, no __init__.py)
│   ├── base/               # framework core: the model toolkit (abstract models, mixins, fields, managers)
│   ├── graphql/            # the GraphQL runtime — schema buckets, auto-CRUD, subscriptions, SDL (`manage.py schema`)
│   └── compose/            # the composer — emits the concrete runtime (`manage.py angee build`)
├── addons/angee/           # base addons shipped with Angee — same `angee.*` namespace, built on the core
│   ├── iam/                # IAM base addon — identity, the swappable user model, the REBAC permission hub
│   ├── resources/          # resources base addon — tiered data import/export (`resources` command)
│   ├── storage/            # storage base addon — drives, folders, content-addressed files, uploads
│   ├── operator/           # operator base addon — bridge to the local operator daemon + admin console
│   ├── integrate/          # integration base addon — the OAuth connection substrate + capability/bridge runtime seam
│   ├── agents_integrate_anthropic/ # Anthropic inference backend addon — SDK client + model sync
│   └── iam_integrate_oidc/ # OIDC login addon — extends integrate's OAuthClient with login fields + composes iam
│       └── …               # an addon may carry a co-located `web/` (e.g. `iam/web` = `@angee/iam`)
├── angee/web/              # frontend framework packages shipped with `django-angee`
│   ├── app/                # `@angee/app` — composition root, routes, providers, addon manifests
│   ├── refine/             # `@angee/refine` — Refine/Hasura transport, live, router, typed-doc glue
│   ├── metadata/           # `@angee/metadata` — `angee.resources` metadata bridge and projection
│   └── ui/                 # `@angee/ui` — rendered binding, primitives, runtime context, views
├── packages/               # dev-only frontend workspace packages
│   ├── storybook/          # `@angee/storybook` — the storybook-first component workshop
│   └── e2e/                # `@angee/e2e` — Playwright e2e harness (`docs/testing/e2e.md`)
├── templates/              # Copier templates — project / stack / workspace / service kinds
│   ├── projects/web/       # project template — scaffolds the host repo (owns the project root)
│   ├── stacks/dev/         # dev Stack template — the `.angee/` overlay (`angee init --dev`)
│   └── workspaces/dev/     # dev Workspace template (`angee ws create … --template dev`)
├── examples/notes-angee/   # the example project `angee dev` runs from the repo root
│   ├── manage.py           # Django entrypoint (`uv run examples/notes-angee/manage.py …`)
│   ├── settings.yaml       # project composition facts and project overrides
│   ├── addons/             # consumer addons for the example
│   │   └── example/notes/  # product logic for the example
│   ├── web/                # the project frontend (Vite + React)
│   └── runtime/            # concrete apps + SDL emitted by the composer — output, not source
├── docs/                   # intent docs — glossary, stack, guidelines, and `docs/howto/`
├── tests/                  # framework tests (composition, GraphQL, IAM, CRUD, …)
├── .agents/                # shared agent methodology — reviewer agents, commands, skills, workflows (`.agents/README.md`; public)
├── .work/                  # private agent work-state — plans, notes, handovers (gitignored symlink to a separate private repo; never mirrored)
├── README.md               # human entry point; `AGENTS.md` is the agent/contributor entry point
├── pyproject.toml, uv.lock                             # Python package + locked graph
└── package.json, pnpm-workspace.yaml, pnpm-lock.yaml   # JS workspace + locked graph
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
  product concept. A REBAC `permissions.zed` relation is the named example: a
  consumer addon adds a company-scoped role to `iam/company` from its own
  `permissions.extends.zed`, and editing the framework's `iam` zed to name a
  domain role (`accountant`, `salesperson`, …) is a bug.
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

`angee dev` is the only supported way to bring the local stack up — do not start
Django, Vite, Daphne, workers, or watchers by hand. Run it from the repository
root: the root stack is wired to the `examples/notes-angee` project, so `angee
dev` there runs that example against the framework.

This repo-root `.angee/` is a **dev-stack overlay** — `ANGEE_ROOT=.angee` with
`local` sources pointing at this checkout. It is one of two stack layouts (the
other being a self-contained instance that clones its sources in); the project
owns the repository root, the stack overlays into `.angee/`. See `docs/glossary.md`
(Project / Project template / Host).

```sh
angee dev            # from the repo root — runs the examples/notes-angee stack
```

`angee dev` is for bringing the long-running stack up. To run a one-shot Django
management command against the example, drive its `manage.py` through `uv` from
the repository root, never by `cd`-ing into the project. The composer is
emit-only; migrations, permission sync, resource data, and GraphQL SDL checks
are separate later steps (a fresh process loads the freshly emitted concrete
models):

```sh
uv run examples/notes-angee/manage.py angee build              # emit runtime sources
uv run examples/notes-angee/manage.py makemigrations base notes
uv run examples/notes-angee/manage.py migrate
uv run examples/notes-angee/manage.py rebac sync               # permissions, after migrate
uv run examples/notes-angee/manage.py resources load           # data, after migrate
uv run examples/notes-angee/manage.py schema                  # write SDL
uv run examples/notes-angee/manage.py schema --check           # SDL, after runtime load
```

For an isolated branch, create a workspace and run the stack inside it. `angee
dev` walks up to the nearest `.angee`, so it works from the workspace root too.

```sh
angee ws create <name> --template dev --input base_ref=main   # branch from main
cd .angee/workspaces/<name>
angee dev
angee ws status      # optional; defaults to the enclosing workspace
```

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
- **Agent work-state — plans, working notes, handovers — goes into `.work/`.**
  `.work/` is a gitignored symlink to a separate, private work-state repo; it is
  never mirrored to the public repo, and nothing in it is published.
- **Durable rules the team must inherit never live only in `.work/`.** A private
  note is invisible to teammates and to the next agent; capture the durable rule
  in the owning `docs/` guideline instead.
