# A5 — Downstream typed-GraphQL codegen emission (execution plan)

## Goal & scope

Make a freshly rendered downstream Angee project emit and consume its own per-project typed GraphQL operations behind the `@angee/gql/<schema>` alias — the same surface that today only works for the in-repo `examples/notes-angee` via hard-pinned dev wiring. After this fork, a project rendered from `templates/` (its addons pulled from `node_modules`, its own `runtime/gql`) typechecks and codegens against `@angee/gql/*` with no edits to the framework repo's example-pinned resolvers.

This is **emit/wiring only**. Per the resolved decisions in `.agents/plans/typed-graphql-operations.md:280-283` and `AGENTS.md:252`, the composer is emit-only and **must not shell out to node** — `angee build` emits Python runtime + (via `manage.py schema`) the SDL; graphql-codegen runs as a **JS build phase** (a pnpm script in the project's web package), ordered by the stack manifest. A5 therefore does **not** add an `angee/compose/` step that runs codegen; it makes the per-project web scaffold (codegen configs + the three alias resolvers + document roots) project-local instead of example-pinned, and proves a rendered project is green.

## Current state (from research)

What ships today is **framework-repo dev wiring only** (`.agents/plans/typed-graphql-operations.md:450-461`):

- **Alias pinned in three resolvers**, all pointing at the example:
  - `tsconfig.base.json:7` — `"@angee/gql/*": ["./examples/notes-angee/runtime/gql/*"]`
  - `examples/notes-angee/web/vite.config.ts:19-26` — `resolve.alias` `^@angee/gql/` → `../runtime/gql/`
  - `vitest.shared.ts:26-33` — `gqlAlias` → `./examples/notes-angee/runtime/gql/` (vitest does not read tsconfig `paths`)
- **Codegen configs live in the example web package only**: `examples/notes-angee/web/codegen.{shared,public,console}.ts`. `codegen.shared.ts:34-39` `DOCUMENT_ROOTS` are **monorepo-layout globs** (`../../../packages/*/src`, `../../../addons/angee/*/web/src`, …) — they will not match addons installed under `node_modules` in a downstream project.
- **The web package owns the codegen script**: `examples/notes-angee/web/package.json:11` — `graphql-codegen --config codegen.public.ts && … codegen.console.ts && node ./bin/build-operation-documents.mjs`. The `.mjs` derives `runtime/gql/<schema>/actions.ts` (action union + AST documents) from the emitted SDL + metadata.
- **Composer preserves but does not wire** the gql tree. `angee/compose/runtime.py:583` excludes `runtime/gql` and `runtime/schemas` from source-drift checks; `clean()`/`reset()` (`runtime.py:227-245`) delete them. The composer emits **no alias**.
- **The stack manifest already orders codegen**: `templates/stacks/dev/template/{{ ANGEE_ROOT }}/angee.yaml.jinja:165-169` has a `codegen` job (`pnpm --dir {{ web_path }} codegen`, `depends_on: [deps, schema]`); `frontend.after` includes `codegen` (`:211`). The **orchestration** exists; the **project's web scaffold** (configs + resolvers) is what is missing/example-pinned.
- **Root ordering** (`package.json:11-17`): `pretypecheck`/`pretest`/`prebuild → pnpm codegen` (no Python→node shell-out). Framework-repo level.

**Key architectural fact**: there is **no per-project web-scaffold template** in `templates/` (only `stacks/`, `workspaces/`, `services/`). A "downstream project" today is a directory like `examples/notes-angee` with a hand-authored `web/` package; `templates/stacks/dev` parameterizes paths to it (`project_path`, `web_path`, `framework_path` — `copier.yml:17-30`). **Architect-decision #1** decides whether A5 ships a new web-scaffold copier template or keeps the example as the canonical scaffold and only de-pins the resolvers.

## What the scaffold must emit per project

So `@angee/gql/<schema>` resolves and typechecks for a rendered project:

1. **`runtime/gql/<schema>/` generation** — produced by the web package's `codegen` pnpm script (NOT the Python composer), reading the per-project `runtime/schemas/<schema>.graphql` from `manage.py schema`. Output per schema: `gql.ts`, `graphql.ts`, `index.ts`, `actions.ts`.
2. **The `@angee/gql/<schema>` alias wired into all three resolvers, pointing at the project's own `runtime/gql`**: the project's tsconfig `paths`, Vite `resolve.alias`, and Vitest `resolve.alias` (vitest ignores tsconfig paths — `vitest.shared.ts:19-20`).
3. **A per-project codegen config** mirroring `codegen.{shared,public,console}.ts`, but with `DOCUMENT_ROOTS` that match the project's install layout (addons under `node_modules`). Flagged by the partial Codex review (`.agents/plans/typed-graphql-operations.md:376`).
4. **The `actions.ts` deriver** (`bin/build-operation-documents.mjs` equivalent) + the `@graphql-codegen/{cli,client-preset}` and `@graphql-typed-document-node/core` deps (already present in the example + root).

## Slices

### Slice 1 — Composer/scaffold change (de-pin + project-relative codegen roots) — decision-gated (#1)

- **1A (recommended): keep the example as the canonical scaffold; make resolvers + document-roots project-relative.** No new copier template.
  - Express the `@angee/gql/*` alias **per-project**: each project's `web/tsconfig.json` supplies its own `@angee/gql/*` → `../runtime/gql/*`; the framework-fixture path (the example) stays in `tsconfig.base.json` as a documented fixture, or moves to a dedicated `tsconfig.fixtures.json` base addons extend (decision #3).
  - Make `codegen.shared.ts:34-39` `DOCUMENT_ROOTS` driven by an override (env var or a small per-project `codegen.{public,console}.ts`) so a downstream project supplies `node_modules/@angee/*/web/src` + its own `./src` + consumer addons, while the example keeps monorepo globs (decision #4).
  - Vite/Vitest: the example's `vite.config.ts` already uses `new URL("../runtime/gql/", import.meta.url)` (project-relative — correct for any project owning its web package). The example-pinned one is **`vitest.shared.ts:26-33`** (`gqlAlias`); a downstream `vitest.config.ts` must supply its **own** gql alias rather than importing the framework's `gqlAlias`.
- **1B (heavier): ship a `templates/projects/web` copier template** rendering the whole web scaffold with alias + document-roots templated to the project layout. The "true downstream" story; materially more work; overlaps with whatever owns the rest of the web scaffold (`index.html`, `src/main.tsx`).

**Recommendation: do 1A** (de-pin so the example and any project that owns a `web/` resolve their own gql); defer the full `templates/projects/web` template (1B) to a follow-on unless the architect rules it in. Either way **no `runtime.py` change runs codegen** (`runtime.py:583` already fences `runtime/gql` out of drift checks).

Determinism: any emitted list (document roots, schema names, alias entries) must be **sorted**, no wall-clock/random/filesystem order (`AGENTS.md:228-229`). The `actions.ts` deriver already sorts.

### Slice 2 — Template change (rendered downstream projects get it)

- Keep/confirm the stack `codegen` job (`angee.yaml.jinja:165-169`) + `frontend.after` ordering (`:211`).
- Ensure the dev SDL hook re-emits `schemas/` before codegen in `angee dev` (already fixed; `codegen` `depends_on: [deps, schema]`).
- 1A: only document `web_path`'s codegen ownership. 1B: add the web-scaffold copier template templated by `web_path`/`project_path`/install layout.

### Slice 3 — Render-smoke verification

Render a project (or, under 1A, treat the de-pinned `examples/notes-angee` as the canonical project) and prove: `manage.py schema` emits `runtime/schemas/<schema>.graphql`; `pnpm --dir <web> codegen` emits `runtime/gql/<schema>/{gql,graphql,index,actions}.ts` **(assert non-empty documents)**; `pnpm --dir <web> typecheck` passes (imports resolve via project tsconfig paths); `pnpm --dir <web> test` passes (vitest resolves via the project's own alias, not the framework `gqlAlias`).

## Gates (from repo root; never `cd` in)

1. `angee build` then `angee build --check` (emit-only invariant).
2. `schema` then `schema --check` (SDL deterministic, no drift).
3. **Downstream render smoke** — codegen → typecheck → test against project-owned `runtime/gql`; assert codegen output non-empty (`ignoreNoDocuments: true` masks empty matches).
4. Framework regression — root `pnpm typecheck` + `pnpm test` (base addons still typecheck against the fixture path after de-pin).

## Orchestration shape

Sequential: Slice 1 → Slice 2 → Slice 3 (slice 3 can't pass until resolvers are de-pinned and roots match the layout). `angee build --check` ∥ `schema --check` ∥ `pnpm typecheck` within the gate.

## Architect-decision points

1. **Scaffold shape**: 1A (de-pin, example stays canonical) vs 1B (new `templates/projects/web` template). Recommend 1A.
2. **Alias scheme for multi-schema projects**: confirm the single wildcard `@angee/gql/*` → `<runtime>/gql/*` remains the contract for N named schemas, and `…/actions` resolves through it.
3. **Framework fixture path** after de-pinning `tsconfig.base.json:7`: keep in `tsconfig.base.json` (documented fixture) or move base-addon typecheck to a dedicated `tsconfig.fixtures.json`.
4. **Document-roots mechanism for downstream**: env-var override vs per-project `codegen.{public,console}.ts` roots vs templated globs.

## Effort / risk

**Effort: M** (most of the seam exists; concentrated de-pinning + generalizing roots + a render-smoke gate; 1B would push toward M/L). **Risk: medium** — changes the resolution contract every downstream web package depends on. Specific risks: a single un-de-pinned resolver yields unresolved imports/ENOENT (vitest is the easy-to-forget one); monorepo globs silently match nothing for `node_modules`-installed addons (render-smoke must assert non-empty output); de-pinning can break base-addon isolated typecheck if the fixture path is dropped not relocated; codegen-against-stale-SDL if ordering is lost. Out of scope (do not regress): operator daemon codegen is separate.

### Critical files
- `tsconfig.base.json`
- `examples/notes-angee/web/codegen.shared.ts`
- `vitest.shared.ts`
- `templates/stacks/dev/template/{{ ANGEE_ROOT }}/angee.yaml.jinja`
- `examples/notes-angee/web/vite.config.ts`
