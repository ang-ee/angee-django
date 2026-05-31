# AGENTS.md

Angee is a thin composition framework for Django + React applications. It binds
boring, proven libraries into one deterministic product surface. Before adding,
replacing, or hand-rolling a capability, check the opinionated stack in
`docs/stack.md`; it is the single source of truth for which library owns what.
The dependency manifests lock the install shape: `pyproject.toml` + `uv.lock`
for Python, and `package.json` + `pnpm-workspace.yaml` + `pnpm-lock.yaml` for
TypeScript.

The framework owns the seams, not the concerns. Product logic belongs in addons.
The composer turns addon contracts into a runnable project. A project contains a
host app; the host composes addons.

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

_Placeholder — this will be expanded as code lands._ The tree will map the
framework core addon, the base addons, the host and composer, and where source
models, generated `runtime/` output, and frontend packages live.

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
- Keep durable agent bookkeeping — notes, plans, handover prompts, commands,
  subagent definitions — in `.agents/` (see `.agents/README.md`), not in `/tmp`
  or other scratch.

## Run From The Root

`angee dev` is the only supported way to bring the local stack up — do not start
Django, Vite, Daphne, workers, or watchers by hand. Run it from the repository
root: the root stack is wired to the `examples/notes-angee` project, so `angee
dev` there runs that example against the framework.

```sh
angee dev            # from the repo root — runs the examples/notes-angee stack
```

`angee dev` is for bringing the long-running stack up. To run a one-shot Django
management command against the example, drive its `manage.py` through `uv` from
the repository root, never by `cd`-ing into the project. The composer is
emit-only; migrations, then resource data, are separate later steps (a fresh
process loads the freshly emitted concrete models):

```sh
uv run examples/notes-angee/manage.py angee build              # emit runtime sources
uv run examples/notes-angee/manage.py makemigrations base notes
uv run examples/notes-angee/manage.py migrate
uv run examples/notes-angee/manage.py resources load           # data, after migrate
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

_TBD — the handoff checklist (checks to run, artifacts to regenerate, what
"verified" means) will be defined here. Until then, run the per-area Checks in the
backend and frontend guidelines and follow "verify before claiming done" above._

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
