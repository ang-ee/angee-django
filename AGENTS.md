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

## Constitution

- Less is more. Better code is the documentation and the example.
- Delegate first. If `docs/stack.md` says a library owns a concern, wire it; do
  not rebuild it.
- Keep one source of truth per fact. Move knowledge to the owning file instead
  of repeating it.
- Compose at build time. Do not monkey-patch, register at runtime, or edit
  generated output.
- Prefer deletion to abstraction. Add an abstraction only when it removes real
  duplication.
- Make extension mechanical: named hooks, explicit owners, deterministic order,
  fail-fast collisions.
- Verify before claiming done. Drift is a bug, whether it is code, docs, schema,
  generated output, or tests.

## DRY

This is framework code. Every impurity in the foundation is copied into addons,
projects, examples, tests, and future decisions. Keep the foundation clean so
the code people copy is the code we want them to write.

When the same idea appears twice, find the owner and remove the copy. Extract a
helper only when it makes the next change smaller and clearer.

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
- Put scratch files, screenshots, and logs only in gitignored locations such as
  `.playwright-mcp/`, `test-results/`, or `playwright-report/`.

## Run From The Root

Always run Angee commands from the project root or an Angee workspace root,
where `angee.yaml`, `pyproject.toml`, and the workspace manifests are visible.

```sh
angee init --dev
angee dev
```

`angee dev` is the only supported local stack entrypoint. Do not start Django,
Vite, Daphne, workers, or watchers by hand.

## Development Process

Every task follows the process and coding principles in `docs/guidelines.md`:
research before building, think in first principles, describe and discuss the
goal, build with the right primitives, and stop when the code grows instead of
getting smarter. Apply it first, then follow the language-specific rules below.

## Guide Split

- The development process and coding principles live in `docs/guidelines.md`;
  follow them for all development work.
- The opinionated stack lives in `docs/stack.md`; manifests lock exact
  dependency setup.
- Backend rules live in `docs/backend/guidelines.md`.
- Frontend rules live in `docs/frontend/guidelines.md`.
- Root rules stay here. Do not duplicate language-specific guidance in this
  file.
