# Web-runtime refactor — execution plan

Refactor of the new frontend-runtime composition slice (uncommitted WIP on
`refactor-claude`). Two goals: (a) apply the review recommendations, and (b)
unify the operator daemon's GraphQL codegen into the same manifest-driven
`runtime/` pipeline so there is one codegen mechanism, not two.

Companion to `refine-greenfield-rebuild-plan.md` (the E3 seam + the "Frontend
runtime manifest slice" checklist item). This file is the work-state; the
durable architecture decision is folded back into that plan on completion.

## Architecture Gate

**Owner map (fact → owner):**
- *Where generated runtime lives* → `ANGEE_RUNTIME_DIR` / `Runtime` (`angee/compose/runtime.py`). One output tree.
- *The generated-file sentinel* → today `runtime.py:29`; **wrong owner** — it gates all generated files (Python, CSS, TS) and is imported back by `frontend.py`, forming a `runtime ↔ frontend` cycle. Correct owner: `angee/fs.py` (already home of `write_atomic`, imported by both `runtime.py` and `sdl.py`).
- *Which addon contributes a web package* → `AppConfig.angee_web_package` (static declaration on the addon). ✔ already correct.
- *Which addon contributes an external GraphQL codegen pass* (operator daemon) → **new** `AppConfig.angee_web_codegen` static declaration on the owning addon. The addon owns *declaring* it; the composer owns *collecting/ordering* it.
- *The set of GraphQL schemas + whether each is live* → the SDL on disk in `runtime/schemas/*.graphql` at codegen time (`getSubscriptionType()` is the truth for `live`). NOT a composer-side disk-glob, NOT a `name == "public"` heuristic, NOT a hardcoded default.
- *The shape of a schema's operation documents* (`actions/aggregates/deletePreviews/groups/revisions`) → `SchemaOperationDocuments` (`packages/refine/src/operation-documents.tsx`, TS). The generator emits one object of that shape; nothing re-enumerates the five kinds in Python.
- *The web package location* → the copier `web_path` input / a settings fact, NOT a hardcoded `../../web` in the composer.
- *Operator daemon SDL* → the daemon (`OperatorDaemon.introspect_sdl()`); acquisition stays the operator addon's ordered build job. The composer never runs the daemon.

**Sibling inventory:** the Django console/public schemas and the operator daemon
schema are the two instances of "a GraphQL schema feeding typed codegen." Today
they have two mechanisms (framework `angee-web-codegen` vs the operator's own
`codegen.ts` + `.gitignore`). This refactor makes them one shape: a manifest
entry with a per-entry config block. `iam_integrate_oidc` etc. are web-package
contributors but not codegen contributors — they stay E3 document authors only.

**Dependency check:** stays one-way. `frontend/web.py` (composer) imports the
sentinel from `angee.fs`, not `runtime.py`. The CLI (`@angee/app`) reads the
manifest; it does not import the composer. No new runtime→build edges.

**Thin-caller check:** `Runtime.build_sources` stays a thin dispatcher; the web
projection lives in the renamed `WebRuntime`. The CLI stays a dispatcher over
manifest entries with no daemon special-case.

**Deletion check (what this unlocks):** delete `DEFAULT_FRONTEND_SCHEMA_NAMES`,
`Runtime._frontend_schema_names`, the composer's `app_ts`/schema-name knowledge
(if app.ts generation moves to the CLI — DECISION 1), `addons/angee/operator/web/codegen.ts`,
`addons/angee/operator/web/.gitignore`, and the per-five-kind enumeration in
`app.ts`. Net should be deletion-positive in Python.

**Naming check:** one concept, one name. The concept is **web** everywhere
(`angee_web_package`, `runtime/web/`, `angee-web-codegen`, `CORE_WEB_PACKAGES`).
Rename the outliers: `frontend.py → web.py`, `FrontendRuntime → WebRuntime`,
`FrontendPackage → WebPackage`.

## Design decisions — RESOLVED

- **DECISION 1 → 1A.** The CLI owns `app.ts`. Composer becomes a pure
  package-graph projector (manifest.json + tailwind.sources.css only). Delete
  `app_ts`, `_frontend_schema_names`, `DEFAULT_FRONTEND_SCHEMA_NAMES`.
- **DECISION 2 → full unification in one pass.** Operator cutover ships with the
  framework changes. Python is pytest-verified here; the operator import flip +
  daemon SDL refresh + Vite resolution are handed off as **UNVERIFIED — run
  `angee dev`** with explicit notes. Execution order is still framework-first
  (verifiable) then operator (needs extra discovery on the `@angee/gql` alias
  scheme before the import flip).

## (original fork text, for the record)

**DECISION 1 — where `app.ts` is generated.**
- *1A (recommended): the CLI owns `app.ts`.* The composer emits only
  `manifest.json` (package graph + documentRoots + external-codegen entries) and
  `tailwind.sources.css` (package sources) — both pure functions of static
  declarations, no schema-name knowledge. The CLI, after SDL exists, globs
  `runtime/schemas/*.graphql`, runs each entry's codegen, derives `live` from the
  SDL, and emits `runtime/web/app.ts`. Result: **no schema-shaped TypeScript is
  authored in Python**; resolves review #2 (composer determinism), #3 (`live`
  heuristic), #4 (five-kind duplication) at the root; deletes `app_ts`,
  `_frontend_schema_names`, `DEFAULT_FRONTEND_SCHEMA_NAMES`.
- *1B: composer keeps `app.ts`.* Smaller change. Collapse the generated
  `actions.ts` to one `operationDocuments` object (app.ts imports one symbol),
  but `live` still can't be sourced from a safe owner at compose time, and the
  composer still emits TS. Leaves #2/#3 partially unresolved.

**DECISION 2 — operator daemon cutover timing.**
- *2A: full unification now.* Add the `angee_web_codegen` contract; operator
  declares its daemon entry; `operator_schema` deposits SDL into
  `runtime/schemas/operator.graphql`; CLI generates `runtime/gql/operator/`;
  ~16 operator TS files flip imports off `src/__generated__/...`; delete
  `codegen.ts`/`.gitignore`; rewire `operator-schema`/`operator-codegen` stack
  jobs. Big and only fully verifiable with a running daemon + pnpm.
- *2B (recommended): land framework side first, operator as verified fast-follow.*
  Ship Phases 1–4 (all framework hygiene + the `angee_web_codegen` contract and
  manifest projection, with the operator still self-codegen'ing) green under
  pytest; then do the operator import cutover (Phase 5) once it can be run.

## Status — COMPLETE (verified end-to-end against the live `angee dev` stack)

Decisions as executed:
- **1A** done: composer (`WebRuntime`) emits only `manifest.json` + `tailwind.sources.css`; the CLI emits `runtime/web/app.ts` (addon imports via the web package's node_modules entry, derived from `--web-root`), derives `live` from the SDL's Subscription type, and emits one `operationDocuments` object per schema.
- **Operator unification (Option B for SDL):** the operator's committed SDL stays in its own package; `angee_web_codegen` records `{schema, package, sdl, documents, types}`; the CLI reads the SDL from `node_modules/@angee/operator/schema/operator.graphql` and generates `runtime/gql/operator/` (client preset + `types.ts`). `runtime/schemas/` stays Django-only (no `GraphQLSdl` prune conflict, no runtime drift). Operator imports flipped to `@angee/gql/operator` / `@angee/gql/operator/types` (the same fixture other addons use). `codegen.ts` + `web/.gitignore` deleted; operator + root `codegen` scripts and the dev-stack `operator-codegen` job removed.

Gates (all green): `pytest` 597 passed; `ruff` clean; `pnpm -r typecheck` 0 errors; `pnpm -r test` all packages incl. operator (29); `manage.py schema --check` ok.

Remaining (noted, non-blocking): the composer's Tailwind `@source` still uses the
`DEFAULT_WEB_ROOT` constant (the *app.ts* import web-root is now derived); a
project with a non-default `web_path` would need it sourced from a setting —
deferred (#5). Operator's `@graphql-codegen/*` devDeps are now unused — optional
removal (needs `pnpm install`).

## Phases

### Phase 1 — structural hygiene (Python, pytest-verifiable)
- [ ] Move `GENERATED_SENTINEL` to `angee/fs.py`; update `runtime.py`, `web.py`, any importer. Hoist `from angee.compose.web import WebRuntime` to module top in `runtime.py` (cycle now broken).
- [ ] Rename `frontend.py → web.py`, `FrontendRuntime → WebRuntime`, `FrontendPackage → WebPackage`; update `runtime.py`, `tests/test_compose.py`, `docs/composer.md`.
- [ ] Restore the rail-order rationale: either a comment in `main.tsx`/the template, or confirm `group` metadata pins order and note it.

### Phase 2 — manifest as the declaration projection (Python)
- [ ] Add `AppConfig.angee_web_codegen` contract (SDL source kind, documents glob, output schema name, codegen config block). Validate fail-fast like `angee_web_package`.
- [ ] `WebRuntime` projects external-codegen entries into `manifest.json` alongside `addonPackages`/`documentRoots`. Deterministic, declaration-sourced.
- [ ] Per DECISION 1: remove schema-name discovery from the composer (1A) or keep + collapse (1B).
- [ ] Source `web_root` from a settings/`web_path` owner (or assert the sibling invariant explicitly); stop hardcoding `../../web`.

### Phase 3 — CLI (`angee-web-codegen.mjs`)
- [ ] Per DECISION 1A: CLI globs SDL, emits `app.ts`, derives `live` from `getSubscriptionType()`, emits one `operationDocuments` object per schema in `actions.ts`.
- [ ] CLI executes per-entry config (django client-preset+authored-docs vs external typescript+client-preset) with no daemon special-case.

### Phase 4 — gate the framework side
- [ ] `uv run examples/notes-angee/manage.py angee build`; `schema`; `schema --check`; `pnpm --dir examples/notes-angee/web codegen` + `typecheck` where runnable. `pytest tests/test_compose.py`.

### Phase 5 — operator cutover (DECISION 2)
- [ ] `operator` declares `angee_web_codegen` (daemon SDL + `documents.daemon.ts` → `operator`).
- [ ] `operator_schema` deposits SDL into `runtime/schemas/operator.graphql`.
- [ ] Flip ~16 operator TS imports from `src/__generated__/{operator,operator-gql}` to `runtime/gql/operator/...`.
- [ ] Delete `codegen.ts` + `web/.gitignore`; drop the `operator-codegen` stack job / fold into `codegen`; reorder so `codegen` waits on `operator-schema`.
- [ ] Verify with a running daemon (`angee dev`): SDL refresh → codegen → typecheck.

### Always
- [ ] Update `docs/stack.md`, `docs/composer.md`, `refine-greenfield-rebuild-plan.md` (E3) to the unified shape. One line in `docs/composer.md` naming both writers of `runtime/` (composer Python + the Node CLI).
