# Focused Review — decompose & optimize `base/graphql`

You are one of three independent reviewers (Claude subagent, Codex, Gemini).
Narrow scope: how should the GraphQL concern be **decomposed, placed, and
optimized**? Two specific suspicions to test, plus a full decomposition proposal.

## Read first
- The code: every `.py` in `src/angee/base/graphql/` (`__init__.py`, `crud.py`,
  `schema.py`, `subscriptions.py`, `introspection.py`). Also `base/asgi.py`,
  `base/urls.py`, `base/views.py`, `base/signals.py` (publishers moved there),
  and `base/apps.py` (`schema_parts`/`SCHEMA_PART_KEYS`).
- The guidelines: `AGENTS.md`, `docs/backend/guidelines.md` (esp. the new
  **Package Layering** section, compose-onto-classes, naming, docstrings),
  `docs/stack.md` (strawberry-django owns GraphQL types).
- The refactor plan: `.agents/plans/2026-05-30-compose-base-resources-refactor.md`
  (the target three packages `compose`/`base`/`resources`, and the build phases:
  SDL is **rendered/emitted at build time** in a run-settings process, while the
  **live** schema is built for serving).

## The two suspicions to evaluate
1. **Placement: some of `graphql` is build-time, not runtime.** `schema.py`
   contains both live-schema building (`build_schema`, used by `views`/`asgi` to
   serve) and SDL rendering/emission for the composer (`render_sdl`, and the
   drift-oriented collection). Under the new layering, which of these belong in
   `angee.compose` (build-time emission/check) vs `angee.base` (runtime serving)?
   Be precise: `collect_schema_parts`/`collect_schema_names`/`build_schema` are
   used on the **serving** path; `render_sdl` (printing SDL to files) is **build**.
   Where should each live, and what is the clean seam?
2. **`subscriptions.py` is doing too much.** It bundles the `ChangeEvent` type,
   the `changes()` surface factory, the channel-layer subscribe stream, REBAC
   read-gating + field redaction, and actor resolution. Propose a decomposition
   into cohesive modules/classes (e.g. event type, subscription factory, the
   channel stream, the gating policy, actor resolution) — say which become classes
   and which stay functions, and which (if any) belong outside `graphql`.

## Also assess (decomposition & optimization across the package)
- `crud.py`: the `crud()` factory + `DeletePreview`/`collect_delete_preview`. Is
  the cascade-preview logic a graphql concern or a model/manager concern? Proper
  class/method shape?
- `introspection.py`: the right home for the Strawberry-internal readers? Shared
  correctly between `crud` and `schema`?
- `schema_parts` contract (declared on `AppConfig`, merged in `schema.py`):
  is the merge logic a missing class (a `GraphQLSchemas`/registry owner)?
- DRY, naming, docstrings, and any dead/unearned surface.

## Output format — STRICT

### Summary
2-4 sentences: the core decomposition problem and the single highest-value change.

### Placement findings (build vs runtime)
For each graphql responsibility: where it lives now, where it should live
(`compose` / `base.graphql` / `base.signals` / a model or manager / elsewhere), and
the seam. Cite `path:line`.

### Proposed `base/graphql` (and neighbors) layout
A concrete target: module list with one-line responsibilities, the classes/methods
each owns, what moves to `compose`, and what `subscriptions.py` splits into.

### Findings
Numbered, severity-ordered (High/Medium/Low): **Title**, **Location**, **Problem**,
**Recommendation**.

### Top recommendations
Ranked, one sentence each.

Be concrete and skeptical. Cite real paths/lines. Judge against the layering rule
(`base` must not import `compose`) and compose-onto-classes.
