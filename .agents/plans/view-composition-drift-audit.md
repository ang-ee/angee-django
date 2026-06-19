# Thin Addons And DRY Framework Audit And Fix Plan

**Date:** 2026-06-19
**Goal:** reduce code by making addons thin, DRY, and consistent, while making
the framework extremely dry, cleanly decomposed, and architecturally disciplined.
Every fact should live at its owner; every addon should declare domain facts and
compose shared primitives; every shared primitive should be named, decomposed,
and reused consistently across the platform.

This is a platform-wide audit and systematic cleanup, not a one-page fix list.

## North Star

The desired end state:

- **Less code overall.** Prefer deletion, composition, and dependency-owned
  behavior over new Angee helpers.
- **Thin addons.** Addons declare domain facts only: model/page intent, fields,
  widgets, action hooks, and domain-specific panels.
- **Dry framework.** `@angee/base`, `@angee/sdk`, `angee.graphql`, and backend
  owners expose small, boring primitives that remove repeated addon code.
- **Clean decomposition.** Behavior lives with the object, library, or layer that
  owns the data and contract.
- **Normalized names.** Classes, methods, variables, files, route ids, menu ids,
  GraphQL fields, and page components use consistent vocabularies across the
  codebase.
- **No local workarounds.** A page-local workaround is treated as evidence of a
  missing owner-level primitive or backend contract.

## Success Looks Like

Success is **less code, fewer choices, and more obvious ownership**.

- **Net deletion.** Platform LOC goes down across the refactor waves. Any LOC
  increase is tied to a named shared owner and unlocks larger deletion. Addon
  files get thinner: fewer local hooks, tables, forms, label maps, option maps,
  query wrappers, SDK wrappers, and one-off helpers.
- **Thin addons.** Addons mostly declare domain facts: models, fields, forms,
  columns, routes, actions, provider/backend choices. Model-backed pages use
  `DataPage` / `List` / `Form` / `GroupListView`. Daemon or in-memory pages use
  a named shared primitive like `RowsListView`, not bespoke table/view state.
  Provider-specific addons hold provider-specific behavior; shared
  agent/integration behavior stays in shared owners.
- **Library leverage.** Researchers can point to places where Angee deleted code
  by leaning on Django, Strawberry, TanStack, urql, React, FastMCP,
  OpenAI/Anthropic SDKs, and the rest of `docs/stack.md`. No addon solves
  something already owned by the stack. `docs/stack.md` stays the dependency
  ownership map, not a wish list.
- **One owner per fact.** Permission logic is not duplicated in
  React/GraphQL/pages. Schema/list/group/filter behavior lives in
  `angee.graphql` / `@angee/sdk` / `@angee/base`, not page-local code. Model
  behavior lives on models/managers/querysets. SDK transport quirks live in
  provider backends, not domain models or generic framework code.
- **Normalized names.** The same concept has the same noun across model, schema,
  route, menu, page, component, file, tests, and docs. Names like `provider`,
  `integration`, `backend`, and `impl` are distinct only when the concepts are
  distinct. Renames reduce confusion instead of adding compatibility aliases
  forever.
- **Guardrails.** The architecture gate is used before structural changes. New
  code has focused tests or grep/lint checks preventing reintroduction of raw
  addon tables, hand-written GraphQL types, duplicate permissions, direct icon
  imports, and other drift.
- **Clear golden path.** A new addon/page should feel almost boring: declare the
  model, schema roots, route, list fields, form fields, group axes, and actions;
  compose shared primitives; run checks. If an agent needs to hand-roll a table,
  custom form state, local GraphQL types, custom permission logic, or SDK
  plumbing, that should fail the architecture gate.

The strongest success signal: a future feature takes fewer lines than the
previous one because the framework has learned the pattern.

## Omission

Call the immediate omission **view composition drift**.

We fixed individual pages and features without enforcing the platform invariant:
model-backed pages must compose the shared `DataPage` / `List` / `Form` /
`GroupListView` primitives and inherit the standard affordances. The immediate
symptom was `/agents/models`: it was a model-backed catalogue but declared only a
flat list, so filters, group-by axes, and board view were missing until we
re-added the shared pattern by hand.

The deeper issue is not "one page forgot a board view." The issue is that view
pages can still bypass the owned composition layer and silently drop expected
capabilities: filters, group-by, board/list switching, visible fields, record
navigation, empty/loading/error surfaces, shared form behavior, and metadata-
derived labels/options.

The broader omission is **architecture and naming drift**: as capabilities were
added, similar concepts grew different shapes in different layers. That produces
extra code, inconsistent names, and unclear decomposition. The fix is not to add
more adapters; the fix is to find the owner, normalize the contract there, and
delete duplicated caller code.

## Reduction Rule

Every fix item must answer:

- What code becomes unnecessary?
- Which owner absorbs the repeated fact or behavior?
- Which callers become thinner?
- Which names become normalized?
- Which tests prove the owner now carries the contract?

If an item adds more code than it deletes, it must justify why the new owner
prevents larger future duplication. Otherwise, stop and choose the smaller
native framework shape.

## Thin View Pattern

A page is thin when it states only addon-owned facts:

- route/component identity
- model/resource name
- list columns and explicit field choices
- form fields, groups, widgets, and create/edit constraints
- truly domain-specific actions or panels
- explicit group/filter defaults only when metadata cannot derive them

A page is not thin when it reimplements shared behavior:

- local CRUD query/mutation plumbing for a model-backed page
- local table/list/board layout
- local filter, grouping, pagination, visible-field, or selection controls
- local form state, required-field handling, relation picker behavior, or JSON
  parsing that the base form layer already owns
- route/open-record state glue that `DataPage routed` owns
- addon-specific empty/loading/error display for normal collection surfaces
- copied enum options, status tones, labels, or relation display strings

## Naming And Decomposition Pattern

Names should reveal the owner and the abstraction level.

- Model-backed page components should follow one page naming shape:
  `<Domain><Plural>Page` for collections, `<Domain><Singular>Page` only when it
  is truly a single-record or bespoke editor.
- Route names, menu ids, file names, and component names should agree on the
  same domain noun. Avoid one concept appearing as `providers`, `inference`,
  `llm`, `models`, and `catalogue` unless those are truly separate owners.
- Backend and frontend field names should use one normalized mapping:
  Django snake case, GraphQL camel case, frontend field paths. Do not introduce
  third aliases in addon code.
- A class or function that mostly inspects one object to decide behavior belongs
  on that object's owner, its manager/queryset, model metadata, or a shared
  primitive.
- File boundaries should match owner boundaries: addon facts in addon files,
  reusable UI behavior in `@angee/base`, resource/schema behavior in
  `@angee/sdk` / `angee.graphql`, domain behavior on the domain model/backend.

Audit naming drift as bugs, not taste:

- [ ] Same concept has multiple names.
- [ ] Same name means different concepts.
- [ ] File name and exported component/class name disagree.
- [ ] Route/menu/page names use different nouns for the same surface.
- [ ] Helper name hides the owner it actually manipulates.
- [ ] Backend and frontend names require caller-side translation that the owner
  could expose.

## Owners

- `@angee/base` owns rendered view composition:
  `DataPage`, `List`, `Form`, `GroupListView`, `RowsListView`, `Field`,
  `Group`, `Column`, toolbar/filter/group/board behavior, and state surfaces.
- `@angee/sdk` owns resource hooks, model metadata, generated GraphQL root-field
  knowledge, authored operation types, and row extraction.
- `angee.graphql` owns aggregate/group/filter GraphQL contracts that shared
  grouped views require.
- Addons own domain facts only: model fields to show, form layout, action hooks,
  and domain panels.

## Current Trigger

- [x] `/agents/models` lacked standard filters, provider/capability group-by,
  and board view.
  - Fixed by adding `GroupListView`, provider filter chips, Provider /
    Capability / Status group options, Provider column, and inference model
    aggregate roots.
  - Keep this as the exemplar: UI affordance gaps on model-backed pages often
    indicate a missing backend aggregate/filter owner plus a page that is not
    composing the shared view primitive.

## Inventory Checklist

Audit every page under:

- [ ] `addons/angee/*/web/src/views/**`
- [ ] `addons/angee/*/web/src/connect/views/**`
- [ ] `examples/notes-angee/addons/*/web/src/**`
- [ ] `examples/notes-angee/web/src/**`
- [ ] `packages/base/src/views/**` for primitive gaps that forced addon forks
- [ ] `packages/base/src/toolbars/**` for missing generic controls
- [ ] `packages/sdk/src/**` for missing metadata/resource owners

For each page, record:

- [ ] Page path and route name.
- [ ] Resource kind: model-backed `DataPage`, in-memory rows, daemon/remote
  transport, custom editor, or genuinely bespoke interactive surface.
- [ ] Current primitive used: `DataPage`, `List`, `RowsListView`, raw table, raw
  form, custom component, etc.
- [ ] Missing standard affordances: filters, custom filters, group-by, board
  view, visible fields, sorting, pagination, selection, bulk delete, routed
  record form, create/edit form, empty/loading/error.
- [ ] Backend owner gaps: missing filter type, missing order type, missing
  aggregate/group root, missing metadata labels/options.
- [ ] Frontend owner gaps: base primitive insufficient, addon bypass,
  duplicated widget/toolbar/state behavior.
- [ ] Code-reduction opportunity: duplicated lines/components/hooks that can be
  deleted after adoption.
- [ ] Naming/decomposition drift: inconsistent class, method, variable, file,
  route, menu, model, or field names.
- [ ] Classification:
  - `adopt-existing-primitive`
  - `extend-base-primitive-first`
  - `add-backend-contract-first`
  - `legitimate-bespoke-view`
  - `delete-dead-or-duplicate-view`
  - `normalize-naming`
  - `move-behavior-to-owner`

## Search Seeds

Use these as starting points, then inspect manually:

- [ ] Raw tables: `rg -n "<table|<Table|TableRow|TableCell" addons examples packages/base/src/views`
- [ ] Local form state: `rg -n "useState\\(|onSubmit|handleSubmit|required|Save failed" addons/*/web examples`
- [ ] Local list plumbing: `rg -n "useResourceList|useAuthoredQuery|pagination|pageSize|filterOptions|groupOptions" addons examples`
- [ ] Missing grouped views: `rg -n "<DataPage|<List|RowsListView|GroupListView" addons examples`
- [ ] Raw GraphQL documents for model CRUD/list pages: `rg -n "query .*\\{|mutation .*\\{" addons/*/web/src examples`
- [ ] Local copy/labels/options: `rg -n "status.*label|enum|options =|toLocale|new Date\\(" addons examples`
- [ ] Name drift: `rg -n "Provider|Providers|Inference|Catalogue|Catalog|Model|Models|Source|Sources" addons packages examples`
- [ ] Owner-smell helpers: `rg -n "get.*For|resolve.*From|build.*For|.*Label\\(|.*Display\\(" addons angee packages examples`
- [ ] Duplicate page shapes: `rg -n "function .*Page|export function .*Page|const .*Page" addons examples`

## Companion Deletion Research Checklists

Run these as separate research tracks before the implementation waves. They are
split out so parallel agents can own a clear surface and return actionable
deletion opportunities instead of producing one broad audit blob.

- [ ] Per-addon deletion and wrong-owner research:
  `.agents/plans/addon-deletion-research-checklist.md`
- [ ] Per-library leverage and framework-concern research:
  `.agents/plans/library-leverage-research-checklist.md`

Each researcher must answer:

- [ ] Which code can be deleted by composing an existing owner?
- [ ] Which code is solving a dependency/framework concern in the wrong layer?
- [ ] Which underlying library feature should we lean on harder?
- [ ] Which shared Angee owner should absorb any unavoidable glue?
- [ ] Which names or files should be normalized while deleting the duplicate?
- [ ] Which focused test or guardrail prevents the code from being reintroduced?

## Fix Waves

### Wave 0: Inventory And Baseline

- [ ] Produce a page inventory table in this plan or a companion
  `.agents/plans/view-composition-inventory.md`.
- [ ] Produce a naming/decomposition inventory in
  `.agents/plans/naming-decomposition-inventory.md`.
- [ ] Complete the per-addon research checklist:
  `.agents/plans/addon-deletion-research-checklist.md`.
- [ ] Complete the per-library research checklist:
  `.agents/plans/library-leverage-research-checklist.md`.
- [ ] Record candidate LOC deletions per slice so the cleanup optimizes for code
  reduction, not abstraction growth.
- [ ] Capture baseline checks:
  - [ ] `pnpm run typecheck`
  - [ ] `pnpm run test`
  - [ ] `.venv/bin/python examples/notes-angee/manage.py schema --check`
  - [ ] relevant Playwright/e2e smoke paths
- [ ] Mark known red checks separately before refactoring.

### Wave 1: Backend Contracts For Shared Views

- [ ] For every model-backed page that needs grouping/board, verify its schema
  exposes filter, order, aggregate, and group roots.
- [ ] Add aggregate roots through `rebac_aggregate_builder`, not ad hoc
  resolvers.
- [ ] Ensure group axes are direct, safe fields or explicitly documented
  aggregate-field/key mappings.
- [ ] Add regression tests for grouped buckets and filter echo.

### Wave 2: Base Primitive Gaps

- [ ] Identify pages that bypass base primitives because the primitive is
  missing a small capability.
- [ ] Extend the owner once in `@angee/base`, then migrate callers.
- [ ] Prefer deleting page-local glue over adding page-local abstractions.
- [ ] Track whether the shared primitive removes net code across adopters.

### Wave 3: Addon Adoption

- [ ] Convert model-backed pages to `DataPage` with nested `List` and `Form`
  declarations.
- [ ] Convert in-memory or daemon collection pages to `RowsListView` or a shared
  wrapper built on it.
- [ ] Remove local tables/forms/toolbars unless classified as legitimately
  bespoke.
- [ ] Use `GroupListView` whenever the model has meaningful group/board axes.
- [ ] Add route/browser smoke for each high-value console section.
- [ ] Delete now-unused local hooks/components/documents after each adoption
  slice.

### Wave 4: Naming And Decomposition Normalization

- [ ] Normalize page component names and file names across addons.
- [ ] Normalize route ids, menu ids, and labels where one domain surface has
  multiple names.
- [ ] Normalize shared primitive names: the same behavior should not be called
  `view`, `page`, `surface`, `panel`, and `section` without a real distinction.
- [ ] Move behavior from loose helpers into the owning class, manager, metadata
  object, backend, or primitive.
- [ ] Delete compatibility aliases once all callers move.
- [ ] Add guidelines/tests for new naming rules only after the code establishes
  the pattern.

### Wave 5: Guidelines And Guardrails

- [ ] Add a terse frontend guideline: model-backed addon pages must compose the
  shared page primitives; missing affordances are primitive/backend gaps, not
  addon-local workarounds.
- [ ] Add a terse naming/decomposition guideline: when the same concept has two
  names, choose the owner vocabulary and migrate callers.
- [ ] Add lint/test guardrails where practical:
  - [ ] raw table use in addon pages requires an allowlist comment or helper
  - [ ] model-backed route pages should use `DataPage`/`List`/`Form`
  - [ ] grouped/board-enabled pages need matching aggregate contracts
- [ ] Document accepted exceptions.

## Parallel Work Shape

- **Agent A: Inventory.**
  - Write set: `.agents/plans/view-composition-inventory.md`,
    `.agents/plans/naming-decomposition-inventory.md`.
  - Reads all frontend/backend page owners and classifies code-reduction,
    naming, and decomposition drift.

- **Agent A1: Addon deletion researchers.**
  - Write set: `.agents/plans/addon-deletion-research-checklist.md` only.
  - Run one researcher per addon or addon cluster.
  - Finds code to delete, wrong-owner logic, duplicate addon-local glue, and
    naming/decomposition drift.

- **Agent A2: Library leverage researchers.**
  - Write set: `.agents/plans/library-leverage-research-checklist.md` only.
  - Run one researcher per locked library or library family in `docs/stack.md`.
  - Verifies whether Angee is reimplementing a library/framework concern and
    identifies cleaner dependency-native patterns.

- **Agent B: Backend contracts.**
  - Write set: addon `schema.py` files, `angee/graphql/**`, backend tests.
  - Adds missing filter/order/aggregate roots for pages that should use grouped
    views.

- **Agent C: Base primitives.**
  - Write set: `packages/base/src/views/**`, `packages/base/src/toolbars/**`,
    base tests/stories.
  - Fixes primitive gaps before addon adoption.

- **Agent D: Addon adoption.**
  - Write set: addon web view files only after Agent C lands needed primitives.
  - Converts pages slice by slice and removes local glue.

- **Agent E: Docs and guardrails.**
  - Write set: `docs/frontend/guidelines.md`, `.agents/plans/**`, optional tests.
  - Starts after first adoption slice proves the exact rule.

- **Agent F: Naming normalization.**
  - Write set: narrow, per-slice source files after owners are identified.
  - Renames classes, methods, variables, files, route ids, menu ids, and GraphQL
    fields only when the owning vocabulary is clear and tests cover references.

## Acceptance Criteria

- [ ] Every model-backed console page either uses the thin pattern or has a
  documented exception.
- [ ] Every page that shows records has the expected standard affordances for its
  resource kind.
- [ ] No addon page reimplements generic filter/group/board/form/list behavior.
- [ ] Backend aggregate/filter contracts exist for grouped model-backed views.
- [ ] Net code decreases across the cleanup, or any local increase unlocks larger
  deletion in follow-up slices.
- [ ] Repeated helper/components are moved to owners or deleted, not renamed in
  place.
- [ ] Names are normalized across classes, methods, variables, files, routes,
  menus, GraphQL roots, and field paths.
- [ ] Every remaining bespoke page has an explicit reason tied to its resource
  kind or interaction model.
- [ ] Guidelines name the rule so future page work starts from the shared
  primitive.
- [ ] Tests and browser smoke cover at least one representative page per addon.
