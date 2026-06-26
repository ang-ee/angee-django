# Refine Greenfield Frontend Rebuild — Plan

Date: 2026-06-24
Status: **Target plan of record. Folded review target. Codex-ready.**

This file is the target shape for the Refine rebuild and is the single target
workflow for future agents. It folds the independent review findings from
`refine-adoption-library-leaning-findings.md`, supersedes the **incremental**
framing of Lanes H–L in
[`refine-adoption-hasura-todo.md`](./refine-adoption-hasura-todo.md) and the
working narration in
[`refine-revolution-pass-ledger.md`](./refine-revolution-pass-ledger.md). Those
files remain historical ledgers/checklists; when they conflict with this file,
this file wins.

The backend work in `refine-adoption-hasura-todo.md` Lanes A–G
(`hasura_resource(...)`, the `angee.resources` artifact, REBAC) **stays**. This
plan rebuilds the **frontend** as a clean Refine-native surface and folds in the
backend owner-corrections from Lane L.

Evidence base (read these first):
- [`refine-adoption-library-leaning-findings.md`](../notes/refine-adoption-library-leaning-findings.md)
  — why the incremental strangler produced a two-engine coexistence + synonym sprawl.
- [`refine-adoption-refactor-plan.md`](./refine-adoption-refactor-plan.md) — the
  settled Hasura dialect + provider decisions (still valid).
- [`refine-revolution-pass-ledger.md`](./refine-revolution-pass-ledger.md) — the
  current branch's completed deletion ledger; use it as evidence, not target.
- Current-thread independent audits are folded into this file, not kept as a
  fourth target: the frontend import/live audits feed Phase 1 and the backend
  owner audit feeds the Backend Rider implementation order.

How to use this file:
- Treat every unchecked item below as target work. Treat checked items as
  current-branch fold-ins that must be preserved during the physical package
  split.
- Update this file before or with any structural Refine slice. Update
  `refine-revolution-pass-ledger.md` with branch history/evidence only.
- If another plan conflicts with this file, change the other plan or add a
  historical note there. Do not fork this target into a second checklist.
- New research notes must be folded here as target shape, then linked as
  evidence. They should not become a second implementation checklist.
- When a parallel agent produces a plan, convert it into owner-specific checked
  or unchecked work in this file. Leave the source note as evidence only.

Current handoff state:
- Frontend package split work is in progress inside the old package names only
  where each slice deletes an old owner and maps directly to the target packages.
- Backend schema-wide Hasura snake-case is the active rider slice. The backend
  `hasura_config()` seam and `pin_snake_wire_names` deletion are in the current
  worktree; the remaining cutover is authored frontend documents/callers/codegen
  that still refer to old camelCase public/custom operation roots.

## Why greenfield, not refactor

A clean structure cannot half-exist: while the old modules are load-bearing,
every new module must accommodate them, which is exactly how `@angee/data` grew a
second GraphQL engine and `@angee/base/src/views/` grew ten names for one concept.
We build the target packages fresh, port addons onto them, and delete the old
packages wholesale — no shims, no re-export façades, no transitional dialects.

The re-gate is deletion, not relocation: a slice is not done because a Refine
owner exists; it is done only when the competing Angee owner is gone in the same
change or explicitly listed below as a short-lived physical-package-split debt.

## Folded research target

The independent library-leaning reviews are no longer a parallel plan. Their
corrections are locked into this target shape:

- [x] Replace the incremental "adopt refine inside Angee" framing with a
  greenfield Refine-native frontend built from the four target packages below.
- [x] Re-gate all completed work on **net deletion**. A box is checked only when
  the competing old owner is deleted or explicitly named as short-lived
  package-split debt.
- [x] Collapse the app data/live/cache path onto Refine + TanStack Query and
  preserve the current deletion of SDK urql, graphcache, relay invalidation, SDL
  metadata parsing, and runtime string-built operation documents.
- [x] Keep urql quarantined only inside the operator daemon surface. It is not an
  app transport, cache, live, or authored-operation owner.
- [x] Make backend `angee.resources` the only frontend metadata source. No app
  SDL boot introspection may return during the package split.
- [x] Close the filter-vocabulary decision: Refine `CrudFilters` is the public
  frontend filter shape; only the Refine/Hasura dialect boundary serializes to
  Hasura bool expressions.
- [ ] Finish the physical package split so the surviving owners are
  `@angee/refine`, `@angee/resources`, `@angee/ui`, and `@angee/app`; delete
  `@angee/sdk`, `@angee/data`, and old `@angee/base` rather than leaving
  façade exports.
- [ ] Finish the backend owner folds from the review: read adapter/aggregate
  generated names from their owners, collapse duplicate Django field classifiers,
  default standard `hasura_resource(...)` glue, and split metadata
  serialization/validation responsibilities.
- [ ] Keep provider decisions final: Refine owns access control, i18next owns
  translations, the Base UI toast renderer backs Refine notifications, TanStack
  Router owns protected-route redirects, and zod remains only if the form
  resolver row has a live consumer.

## Decisions (locked 2026-06-24)

- **Four packages, fresh names:** `@angee/refine` · `@angee/resources` ·
  `@angee/ui` · `@angee/app`. Distinct from `@angee/sdk` / `@angee/data` /
  `@angee/base` so old and new never get confused during the port.
- **`@angee/resources` is the framework metadata bridge.** The existing platform
  Resources addon web package is renamed to `@angee/resources-addon`; addon
  identity may still be `id: "resources"`, but the npm package name no longer
  competes with the framework owner.
- **Cutover:** build alongside the current packages; port each addon onto the new
  stack (deleting that addon's old web code in the same change); delete
  `@angee/sdk` + `@angee/data` + old `@angee/base` in one final cut. Gates stay
  green throughout.
- **Frontend-only rebuild** plus the backend owner-folds in "Backend rider" below.
  The backend `hasura_resource(...)` + `angee.resources` artifact + REBAC are the
  right owners and are NOT rebuilt.
- **No compatibility fallbacks.** Old public frontend words/contracts are deleted,
  not aliased: `DataPage`, `DataView`, `ListViewState`, `ShellConfig`,
  `ShellChromeProps`, `ConsoleShell`, `PublicShell`, SDK CRUD hooks, route-authored
  breadcrumbs, and runtime string-built GraphQL document helpers.
- **Granular date and JSON drill-down are required capabilities.** The backend
  resource/Hasura dialect owner declares dimensions, granularities, bucket keys,
  and drill-down predicates. The frontend reads resource meta and forwards the
  backend-authored filter/where; it must not invent local date/JSON group
  semantics.
- **Refine `CrudFilters` is the frontend filter vocabulary.** Angee does not keep
  a second public filter dialect between Refine and Hasura. UI controls emit
  Refine filters; the Hasura bool-expression serializer lives at the
  `@angee/refine` dialect boundary, with backend-authored group drill-down
  predicates flowing through that same path.

### Current Branch Fold-In

The current `refactor-refine` branch has already completed several target-shape
deletions inside the existing package names. Do not rebuild these as a second
parallel layer; when the package split happens, move them to the target packages
or delete the old package shell.

- [x] One composed `<Refine>` root owns resources, router provider, data
  providers, i18n provider, live provider, and route/resource parsing.
- [x] Public page ownership is Refine resource actions:
  `ResourceList`, `ResourceCreate`, `ResourceEdit`, `ResourceShow`.
- [x] Route-authored breadcrumbs are deleted; chrome renders Refine
  `useBreadcrumb()`.
- [x] Chrome/menu rendering reads Refine `useMenu()` output.
- [x] Model-backed list surfaces are rebound to `@refinedev/react-table`;
  local/in-memory rows stay outside Refine as explicit non-resource adapters.
- [x] `FormView` is rebound to `@refinedev/react-hook-form` /
  `react-hook-form`.
- [x] Generic list/record/mutation/action/authored-query hooks moved off
  `@angee/sdk` and onto Refine/custom-operation glue in `@angee/data`.
- [x] Single-id `{ ok, message }` action mutations now execute from generated
  per-schema action document registries. `useActionMutation` no longer builds or
  parses action GraphQL at runtime; hosts pass generated `operationDocuments`
  through `createApp`.
- [x] Delete-preview mutations now execute from generated per-schema resource
  document registries. `deletePreviewRequest(...)` requires a generated document,
  all base/storage/knowledge callers resolve it through `OperationDocumentsProvider`,
  and `@angee/data` no longer exports/builds/parses delete-preview GraphQL at
  runtime.
- [x] Revision queries now execute from generated per-schema resource document
  registries. `revisionsRequest(...)` requires a generated document,
  `useResourceRevisions` resolves it through `OperationDocumentsProvider`, and
  `@angee/data` no longer exports/builds/parses revision GraphQL at runtime.
- [x] Aggregate queries now execute from generated per-schema resource document
  registries. `aggregateRequest(...)` requires a generated document,
  `useAngeeAggregate` resolves it through `OperationDocumentsProvider`, and
  the generated document selects backend-declared aggregate measures from
  `angee.resources`.
- [x] Model list fetches no longer use `@angee/data` runtime GraphQL documents.
  Base list callers pass provider-native `meta.fields`, `filters`, and `sorters`
  to Refine `useList` / `@refinedev/react-table`, so the stock
  `@refinedev/hasura` provider owns list documents and aggregate-count documents.
- [x] Group queries now execute from generated per-schema resource document
  registries. `groupByRequest(...)` requires a generated document,
  `useAngeeGroupBy` resolves it through `OperationDocumentsProvider`, and the
  generated document selects backend-declared group keys, date ranges, JSON keys,
  and aggregate measures from `angee.resources`.
- [x] Facet queries no longer build aliased GraphQL documents at runtime.
  `useAngeeFacets` runs provider-native TanStack `useQueries` over generated
  group documents, so each facet is a normal Refine custom query with backend-
  owned group variables and extraction.
- [x] `@angee/data` no longer depends on `@angee/sdk`.
- [x] SDK relay invalidation, relay registry, arbitrary document query/mutation/
  subscription wrappers, typed-document exports, stable-deps, disabled-documents,
  `graphql-client.ts`, `graphql-provider.tsx`, `cache-config.ts`,
  `schema-object-types.ts`, and the SDL metadata parser are deleted.
- [x] SDK relay/refetch ownership is not recreated in `@angee/data`: resource
  invalidation uses Refine invalidation/live semantics, authored custom query
  invalidation is tagged through TanStack Query metadata, and the only arbitrary
  subscription surface is the package-local operator daemon quarantine.
- [x] App metadata is projected from backend `angee.resources`, not SDL boot
  introspection.
- [x] Backend resource metadata now emits enum value inventories, so enum
  labels/options are artifact-owned instead of parser-owned.
- [x] Operator daemon arbitrary subscriptions are quarantined inside
  `@angee/operator`; no app or SDK dependency on the daemon urql transport.
- [x] Public `GroupListView` is deleted; `ListView` is the single
  grouped-capable resource list surface and addon pages no longer opt into a
  separate grouped list component.
- [x] Active docs/source comments no longer teach SDK-owned app transport,
  app urql, or SDL metadata ownership. Typed operation-document helpers are
  documented from `@angee/refine`; urql is documented as operator-daemon-only
  quarantine.
- [x] Refine `accessControlProvider` is mounted from backend resource
  `capabilities`; standard Refine actions are mapped to resource capabilities
  and custom rendered delete flows honor `useCan`.
- [x] Refine i18n provider is backed by `i18next`; the dependency lives at the
  data/refine owner, not SDK.
- [x] Refine `notificationProvider` is mounted over the Base UI toast renderer;
  refine CRUD hooks now notify through the shared feedback surface instead of a
  parallel notification owner.
- [x] Sign-in gating for protected layouts is implemented with TanStack Router
  `beforeLoad` redirects; the old rendered `RequireAuth` effect bounce is
  deleted.
- [x] `validateRoutes` string rules are deleted. The addon route-tree builder
  still fails fast for unknown parent/layout declarations, but declared parent
  route objects now own nesting via `getParentRoute`.
- [x] Route-static chrome derivation is deleted. Layout chrome props no longer
  carry route-authored `title`/`icon`; labels/icons live in Refine resource
  metadata and menu resources.
- [x] Notes nested record routes now project through the generated Refine
  resource (`notes.Note` -> `/notes`), so `/notes/:id` breadcrumbs come from
  Refine resource/action state without static chrome.
- [x] Notes JSON group keys and granular date/JSON drill-down smoke are green:
  `tags` is emitted as a JSON group key, exact JSON bucket equality is used for
  drill-down, and date day drill-down is browser-smoked through the Refine list.
- [x] Record-action browser pass outside Notes is green for seeded Storage,
  Knowledge, Integrations, and Agents surfaces. Storage/Knowledge custom actions,
  Integrate action menus, and Agents provision/delete surfaces render through the
  shared resource/action path.
- [x] Integrate duplicate local action buttons are removed where shared
  resource/action surfaces own the operation; OAuth connect has one canonical
  callback path (`/integrate/oauth/callback`).
- [x] Agent lifecycle/delete eligibility is backend/model-owned and projected to
  the frontend as metadata/booleans instead of being decoded locally.
- [x] Unique Agents collection routes claim their Refine resources; filtered
  alternate views intentionally do not claim duplicate resources.
- [x] Public label helpers no longer leak out of list internals; shared labels
  live in the base lib owner and list fallback helpers stay private.
- [x] The physical package split has started without façade exports:
  `@angee/refine` owns the Refine/Hasura field/filter/sort codec, generated
  operation request/extraction helpers, operation document context/types, and
  typed-document helpers; `@angee/data` imports those owners instead of
  re-exporting or duplicating them.
- [x] `@angee/refine` owns the physical Hasura provider/live-provider and
  transport-auth helpers. `@angee/data` no longer exports provider/client/live
  helpers, no longer has provider/transport files, and no longer carries Hasura,
  GraphQL request/WebSocket, form, table, or zod dependencies that belonged to
  the moved/old owners.
- [x] `@angee/refine` owns the physical TanStack Router provider bridge.
  `@angee/data` no longer exports router helpers, no longer has router files,
  and no longer carries a TanStack Router dependency.
- [x] `@angee/resources` owns the physical generated resource artifact
  contracts, React metadata providers/context, schema-field metadata projection,
  Refine resource projection, row identity helpers, and open resource type
  contracts. `@angee/data` no longer exports or hosts `metadata.tsx`,
  `resources.ts`, `resource-types.ts`, or `rows.ts`.
- [x] `@angee/resources` owns resource capability mapping to Refine access
  control (`createAngeeAccessControlProvider` / `capabilityForRefineAction`).
  `@angee/data` no longer exports or hosts access-control glue, and
  `@angee/resources` no longer depends on `@angee/refine`.
- [x] Invalidation ownership is split by concern: `@angee/resources` maps backend
  model labels to Refine resource invalidation targets, and `@angee/refine`
  owns the authored-query TanStack metadata exception. `@angee/data` no longer
  hosts an invalidation owner.
- [x] Stable dependency helpers for Refine custom-operation hooks live in
  `@angee/refine`; `@angee/data` no longer hosts `stable-deps.ts`.

Target package mapping for already-landed code:

| Current branch owner | Target owner |
|---|---|
| `@angee/data` provider/transport/live/router/custom ops | `@angee/refine` |
| physical `@angee/resources` metadata/resource projection/rows/resource types | `@angee/resources` |
| `@angee/data` access control/auth/authored hooks/i18n/invalidation remnants | `@angee/resources`, `@angee/refine`, or `@angee/app` by concern |
| `@angee/base` rendered views/chrome/widgets/feedback | `@angee/ui` |
| `@angee/sdk` addon composition/runtime context/registries | `@angee/app` or a tiny contracts module if needed to avoid cycles |

Rule for continuing before the physical package split: a change may remain in
the existing package only if it deletes an old owner in the same slice and maps
cleanly to the target package above. No compatibility aliases or fallback
exports.

### Active Continuation Queue

Use this queue when resuming the current branch before the physical package
split. Items here are allowed to land inside current package names only because
they delete old owners and map directly to the target packages above.

- [x] Finish generated operation documents for delete-preview mutations:
  `operationDocuments.deletePreviews` is generated per schema, every
  `deletePreviewRequest(...)` caller passes the generated document, and
  `@angee/data` no longer exports/builds/parses a delete-preview GraphQL string.
- [x] Move the remaining runtime string-built operation helpers away from
  runtime string assembly: group requests use generated `TypedDocumentNode`
  registries from `angee.resources`, and facets run as provider-native
  multi-query executions over those generated group documents.
- [x] Move backend-owned group drill-down semantics into resource/Hasura metadata:
  date granularities and JSON bucket equality return an authored filter/where;
  `ListView` forwards it instead of reconstructing local semantics.
- [x] Collapse Hasura serialization onto Refine `CrudFilters`: delete the
  duplicate `ResourceViewFilter -> Hasura where` switch, route custom group/
  facet/aggregate `where` through `crudFiltersFromFilterRecord(...)` plus one
  `hasuraWhereFromCrudFilters(...)` serializer, and remove the old
  `refineFiltersFromAngeeFilter` / `hasuraWhereFromAngeeFilter` exports.
- [ ] Collapse the remaining private URL/filter-record shorthand fully into
  Refine/TanStack table search state during the physical package split; no public
  Angee filter vocabulary may survive in `@angee/refine` or `@angee/resources`.
- [x] Start the physical package split with real owner deletion, not a façade:
  `@angee/refine` now owns the Refine/Hasura field/filter/sort codec,
  `@angee/data` no longer exports it, and callers import the new owner directly.
- [x] Move generated operation request/document/typed-document helpers into
  physical `@angee/refine`: `operations.ts`, `operation-documents.tsx`, and
  `typed-document.ts` moved out of `@angee/data`; the old runtime selection
  helper was deleted; operation/document contracts are no longer exported from
  `@angee/data`.
- [x] Move Hasura provider/live provider and transport-auth helpers into
  physical `@angee/refine`: `provider.ts`, `provider.test.ts`, and
  `transport-auth.ts` moved out of `@angee/data`; consumers import the new owner
  directly; `@angee/data` dependency leftovers for old form/table/provider
  owners are removed.
- [x] Move the TanStack Router provider bridge into physical `@angee/refine`:
  `router.tsx` and `router.test.ts` moved out of `@angee/data`; app/storybook
  callers import the new owner directly; the old data export and dependency are
  removed.
- [x] Move generated resource artifact contracts and resource projection into
  physical `@angee/resources`: `metadata.tsx`, `resources.ts`,
  `resource-types.ts`, and `rows.ts` moved out of `@angee/data`; app/base/addon/
  storybook callers import the new owner directly; old data exports/files are
  removed.
- [x] Remove any stale `@angee/resources -> @angee/refine` package edge during
  the Phase 2 cleanup. The resource package may depend on `@refinedev/core`
  types and React context, but it must not depend on the Hasura dialect package
  or inspect dialect request helpers.
- [x] Split `@angee/data` invalidation glue by owner: resource invalidation
  target projection moved to physical `@angee/resources`; authored-query
  metadata tagging moved to physical `@angee/refine`; the old
  `@angee/data/src/invalidation.ts` file is deleted.
- [x] Move stable dependency helpers used by authored custom-operation hooks into
  physical `@angee/refine`; delete `@angee/data/src/stable-deps.ts`.
- [x] Finish the schema-wide Hasura snake-case cutover after the backend
  `hasura_config()` seam: authored public/console operation documents and result
  accessors use snake_case roots (`available_connections`, `login_start`,
  `login_complete`, `connect_account_start`, `connect_account_complete`); codegen
  regenerates with no diff; the frontend gate is green. No camelCase aliases.
  Collateral fix: a blanket `roleCount→role_count`/`modelCount→model_count` rename
  had leaked into i18n keys (iam `OverviewPage`/`SchemaPage`) and stale platform
  detail-test mocks (`usePlatformModel` returns `model`, not `resource`); both
  corrected — wire names are snake_case, i18n keys/row shapes stay their own
  vocabulary. `schema --check`, codegen, `pnpm typecheck`, and full `pnpm test` green.

## Governing patterns (the spec — every module obeys these)

**Decomposition:**
- **P1 Rent the generic, own the semantic.** CRUD, cache, table/form state,
  routing, menu, breadcrumb, auth, i18n, notifications, the access gate are rented
  (Refine / TanStack / react-hook-form / i18next). Angee owns only the
  metadata→config projection and the domain presentation.
- **P2 One-way layered DAG;** one concern per package; no upward reach.
- **P3 The backend `angee.resources` artifact is the single source of truth.**
  Exactly one package (`@angee/resources`) consumes it; no SDL introspection
  anywhere; everything above reads typed config.
- **P4 Refine `resources` + open `meta` IS the registry.** No parallel Angee
  entity registry; Angee facts (capabilities, group dims, shell taxonomy, label,
  icon) ride `resource.meta`.
- **P5 Codegen `TypedDocumentNode` only.** No runtime string-built GraphQL, no
  runtime `parse()` document assembly, and no "temporary" dialect exception. If
  a Hasura dialect document varies by resource, generate it at build/codegen time
  from `angee.resources`; project document roots come from the generated
  `runtime/web/manifest.json`, not host-maintained globs.
- **P5b One filter serialization boundary.** URL/table/form filter state is
  Refine/TanStack state. The only Angee filter logic above the backend is the
  `CrudFilters -> Hasura _bool_exp` serializer in the Refine dialect package,
  plus backend-authored group drill-down predicates.
- **P6 Headless/rendered split.** View-state binds Refine hooks headlessly;
  rendered composes shared primitives, never hand-rolls a grid/form.
- **P7 One name per concept, in Refine's vocabulary** (resource/action/list/show/
  edit/create).

**Extension — the ONLY seams an addon may use:**
- **E1** project a backend resource → Refine `resource` + `meta`.
- **E2** contribute to a keyed, sequenced, fail-fast registry (slot / widget /
  form / preview / icon) — deterministic order, collisions error at build.
- **E3** author codegen documents routed to a schema (public / console / operator);
  Django `AppConfig.angee_web_package` declares which addon package contributes
  frontend documents, and `AppConfig.angee_web_codegen` declares an external
  GraphQL schema (the operator daemon) that joins the one `angee-web-codegen`
  pass — its committed SDL read from the addon package, generated into
  `runtime/gql/<schema>/` like every other schema.
- **E4** declare routes → TanStack route tree + Refine resource actions.
- **E5** gate on capabilities via `accessControlProvider` reading backend caps.
- **E6** register an i18next namespace bundle.

No monkey-patch, no runtime registration, no object-shape probing.

## The package stack (DAG, concern, rents/owns, modules)

```
rented:  @refinedev/core · @refinedev/hasura · graphql-request/ws
         TanStack Router/Table · react-hook-form/zod · i18next · lucide · Base UI
   │
@angee/refine     Hasura-dialect Refine binding
   │
@angee/resources  metadata → Refine config bridge
   │
@angee/ui         rendered binding (+ headless view-models)
   │
@angee/app        composition + app shell (depends on all of the above)
   │
@angee/<domain>   addons: pages + codegen documents
```

| Package | Owns | Rents | Modules |
|---|---|---|---|
| **@angee/refine** | the parts of a Refine+Hasura app every project shares, with **zero domain/metadata knowledge** | `@refinedev/hasura`, graphql-request/ws, TanStack Router | `provider` (named dataProvider map + idType/namingConvention) · `transport` (graphql-request + session/CSRF/service auth) · `live` (graphql-ws liveProvider) · `router` (TanStack routerProvider) · `operation-documents` / `typed-document` contracts · `dialect/{action,aggregate,groupBy,facets,deletePreview,revisions}` (typed codegen docs + hooks over `useCustom`) |
| **@angee/resources** | the **only** consumer of `angee.resources`; metadata→typed config | refine-core types | `artifact` (load/validate/narrow) · `project` (→ Refine `resources[]` + `meta`) · `fields` (ONE kind/scalar/widget classifier, reads the artifact, not SDL) · `dimensions` (group/facet specs, date granularity, JSON drill-down filter specs → `@angee/refine` dialect params) · `capabilities` (per-action caps → accessControl) · `contracts` (open `ResourceTypeMap`, types) |
| **@angee/ui** | the single rendered binding + headless view-state | Refine `useTable`/`useForm`/`useMenu`/`useBreadcrumb`, Base UI, TanStack Table | `views/list` (+`list/modes`) · `views/form` · `views/record` · `views/relation` · `views/visualizations` · `views/model` (headless view-state) · `chrome` (rail/topbar/breadcrumb/spotlight) · `widgets` · `feedback` (toast = the notification renderer) · `primitives` (Base UI binding) |
| **@angee/app** | assembles the app; the only package depending on all the above | Refine `<Refine>`, i18next, TanStack Router, GraphQL Code Generator | `define-addon` · `compose` · `create-app` · `providers/{auth,i18n,notification,accessControl}` · `routing` (addon routes → TanStack tree) · `registries` (slot/widget/form/preview/icon) · `codegen` (`angee-web-codegen` over `runtime/web/manifest.json`) · `shell` (app-rail taxonomy, landing) |

## Canonical vocabulary (settled once, in @angee/ui)

View primitives — **one name per concept** (P7):
- **`ResourceList`** — server resource action surface (open/create/edit, refine-backed).
- **`ListView`** — the single render surface; **grouping on by default** (no
  `GroupListView`/`GroupedList`/`ListInternals`/`ResourceListFrame` as public names —
  those become private parts under `views/list/`).
- **`RowsListView`** — in-memory rows; **`RelatedRowsList`** / **`AuthoredRowsList`** its
  two data adapters.
- **`List`** — the declarative page-DSL element.
- **`FormView`**, **`RecordView`** — form / detail surfaces.
- Mode siblings **`BoardView`/`GalleryView`/`TimelineView`** under `views/list/modes`;
  standalone **`TreeView`/`GraphView`/`DashboardView`** under `views/visualizations`.

Headless view-state modules use a consistent name set under `views/model/`:
`view-state.ts` (the state machine), `use-table-surface.ts` (the refine/TanStack
table hook), `view-context.tsx` (React context).

## Addon contract (one mechanical template)

```
addons/angee/<domain>/web/src/
  pages/<Resource>Page.tsx      # composes @angee/ui primitives; thin (declare + dispatch)
  data/documents.<schema>.ts    # codegen-authored TypedDocuments, schema-routed (E3)
  lib/                          # domain utils
  i18n.ts                       # one i18next namespace bundle (E6)
  index.ts                      # single defineAddon seam: routes (E4) + registry contributions (E2)
```

An addon adds behavior only through E1–E6. That is the entire extension surface.
`parties` / `messaging` root pages and the loose `iam` / `agents` helpers move into
this template; document the template in `docs/frontend/guidelines.md`. Frontend
presence is declared once on the backend owner with
`AppConfig.angee_web_package`; the composer emits `runtime/web/manifest.json`,
`runtime/web/tailwind.sources.css`, and `runtime/web/app.ts`.

## Provider wiring (the prior-art adoptions, in @angee/app Phase 4)

- **accessControlProvider** `can({resource,action})` reads the **backend capability
  metadata** projected by `@angee/resources/capabilities` (E5). Replaces ad-hoc
  `canDelete`/provision eligibility; standard buttons auto-gate. **Do NOT adopt
  CASL** (server owns permissions).
- **i18nProvider = i18next-native**: `addResourceBundle(lng, ns, bundle, /*deep*/ true)`
  + `fallbackNS` + native `t(key, vars)`. Delete the hand-rolled merge/
  interpolation/fallback engine; keep only the build-time collision check.
- **notificationProvider** `{open, close}` over the `@angee/ui/feedback` Base UI
  toast, so refine CRUD hooks auto-notify. Keep Base UI as the renderer.
- **Sign-in gate via TanStack `beforeLoad`** redirect (`throw redirect({to:'/login',
  search:{next}})`); drop the `RequireAuth` `useEffect` bounce. Post-action landing
  via Refine `options.redirect`.
- **Drop `validateRoutes` string rules**; TanStack Router types + `getParentRoute`
  own parent/prefix correctness. Keep the addon-decl→tree builder as the seam.
- **Resource label/icon on `resource.meta`** (E1); chrome reads via `useMenu`/
  `useResource`/`useBreadcrumb`. Delete the menu-trail breadcrumb and menu-derived
  chrome (the parallel source).
- **Icons:** lucide `DynamicIcon` for dynamic names; the registry keeps only
  non-lucide addon glyphs + fallback.
- **Keep bespoke (no honest owner):** the slot/contribution registry, the app rail
  (dnd-kit owns the drag), the preview MIME dispatch, the widget/field DSL over
  react-hook-form+zod. Do not library-ize these.

## Build Order (Checklist)

Each phase has a deletion target and a green gate. Do not mark a phase complete
because the new owner exists; mark it complete only when the old owner is gone or
explicitly listed here as short-lived package-split debt.

### Phase 0 — Scaffold

- [x] Create `@angee/refine`, `@angee/resources`, `@angee/ui`, and `@angee/app`
  with package manifests, tsconfigs, barrels, and one-way workspace deps.
- [x] Wire the workspace and lockfile.
- [x] Gate: `pnpm install` and an empty-package typecheck pass.
- [x] Current branch maps existing package code to target owners above; the
  physical split has begun with the Refine dialect codec, operation contracts,
  Hasura provider/transport helpers, the TanStack Router bridge, and the
  resource artifact/projection/row/type contracts, while the remaining
  resources/UI/app moves are still tracked below.

### Phase 1 — `@angee/refine`

- [x] Fold in current provider/transport/live/router work from `@angee/data`.
- [x] Fold in generated single-id action mutation documents.
- [x] Generate delete-preview documents per schema/resource and remove the
  runtime delete-preview GraphQL builder/export.
- [x] Generate revision documents per schema/resource and remove the runtime
  revision GraphQL builder/export.
- [x] Generate aggregate documents per schema/resource and remove the runtime
  aggregate GraphQL builder/export.
- [x] Delete `@angee/data` list document construction by rebinding model list
  callers to provider-native Refine/Hasura `meta.fields`, `filters`, and `sorters`.
- [x] Generate group dialect documents from `angee.resources` and remove the
  runtime group GraphQL builder/export.
- [x] Provider-own facet dialect execution by running each facet through the
  generated group document; remove remaining runtime `parse()` and string
  document assembly from `operations.ts`.
- [x] Keep the dialect package metadata-free: it accepts generated documents and
  dialect variables, but does not inspect Angee resource artifacts directly.
- [x] Move Refine/Hasura field selection, sorter, and `CrudFilters -> Hasura`
  bool-expression serialization into physical `@angee/refine`; delete the old
  `@angee/data` export and test there.
- [x] Move generated operation request/extraction helpers, operation document
  context/types, and typed-document helpers into physical `@angee/refine`;
  delete old `@angee/data` exports and the obsolete runtime selection helper.
- [x] Move Hasura provider/live provider and transport-auth helpers into physical
  `@angee/refine`; delete old `@angee/data` exports/files and stale manifest
  dependencies for moved/old provider/form/table owners.
- [x] Move TanStack Router provider bridge into physical `@angee/refine`; delete
  old `@angee/data` exports/files and the stale TanStack Router dependency there.
- [ ] Make authored custom-query live refresh explicit on Refine primitives:
  queries that declare `models` must refresh through Refine live/manual
  subscription semantics or a named TanStack-query metadata exception. Do not
  rebuild the deleted SDK relay/refetch registry under `@angee/refine`.
- [ ] Gate: fixture schema unit tests plus a smoke list/aggregate through the
  stock `@refinedev/hasura` provider.

### Phase 2 — `@angee/resources`

- [x] Fold in app metadata projection from backend `angee.resources`.
- [x] Fold in backend enum inventories and resource capability projection.
- [x] Fold in Notes JSON group metadata and Refine resource route projection.
- [x] Move the current generated artifact contracts, metadata context/providers,
  schema field projection, Refine resource projection, row identity helpers, and
  open `ResourceTypeMap` contracts into the physical package; delete old
  `@angee/data` exports/files.
- [ ] Finish the Phase 2 owner split inside physical `@angee/resources`: carve
  artifact load/validate/narrow, one field classifier, dimensions, capability
  extraction, and resource projection into named modules instead of leaving a
  monolithic metadata owner.
- [x] Move access-control/capability provider glue out of `@angee/data`.
  Resource capability-to-Refine-action mapping and the small provider factory
  live in `@angee/resources`; the app composition layer mounts the returned
  Refine provider.
- [x] Move resource invalidation target projection out of `@angee/data` and
  into `@angee/resources`; keep authored-query cache metadata in `@angee/refine`
  as the named TanStack-query exception for custom operations.
- [x] Ensure granular date and JSON drill-down specs are artifact-owned and
  produce backend-authored filter/where params for `@angee/refine`.
- [ ] Give metadata contracts their own serialization/validation owner; delete
  hand-transcribed dict mirrors and split merge/validate/introspection concerns
  out of monolithic metadata modules.
- [ ] Gate: snapshot projected Notes resources, classifier tests, dimension
  tests, and capability extraction tests.

### Phase 3 — `@angee/ui`

- [x] Fold in Refine-backed `ListView`, `FormView`, menu, breadcrumb, feedback,
  and route-static chrome deletion.
- [x] Fold in the canonical public resource action names:
  `ResourceList`, `ResourceCreate`, `ResourceEdit`, `ResourceShow`.
- [ ] Move the canonical view primitives, headless view-state, widgets, feedback,
  and chrome into the physical package.
- [ ] Delete remaining public synonym exports after porting callers.
- [ ] Keep grouped list rendering metadata-driven; no local date/JSON group
  semantics may remain in public UI code.
- [ ] Gate: vitest/storybook plus one model-backed
  `ListView`/`FormView`/`RecordView` fixture render.

### Phase 4 — `@angee/app`

- [x] Fold in one composed `<Refine>` root.
- [x] Fold in access-control provider from resource capabilities.
- [x] Fold in i18next provider, notification provider, TanStack
  `beforeLoad` sign-in redirects, and deletion of `validateRoutes` string rules.
- [ ] Move `defineAddon`, `composeAddons`, `createApp`, providers, route-tree
  builder, registries, and app-shell taxonomy into the physical package.
- [ ] Place addon manifest/runtime contracts here by default; introduce a tiny
  contracts module only if a real package cycle appears.
- [ ] Delete the hand-rolled i18n merge/interpolation/fallback owner; keep
  i18next-native plus build-time namespace collision checks. Confirm `zod` owns
  form resolver validation and keep `valibot` scoped only to JSON-scalar
  narrowing, or remove unused dependencies/stack rows together.
- [ ] Gate: a one-addon app boots with access gate, i18n, notifications, router
  parsing, menu, breadcrumb, and sign-in redirect all wired.

### Phase 5 — Port Addons

- [ ] Port notes and delete its old web code in the same change.
- [ ] Port iam and delete its old web code in the same change.
- [ ] Port storage and delete its old web code in the same change.
- [ ] Port knowledge and delete its old web code in the same change.
- [ ] Port integrate and delete its old web code in the same change.
- [ ] Port agents and delete its old web code in the same change.
- [ ] Port parties and delete its old web code in the same change.
- [ ] Port messaging and delete its old web code in the same change.
- [ ] Port operator console and keep daemon transport package-local.
- [ ] Gate per addon: typecheck/test/build + browser/e2e smoke for the owned
  surfaces + REBAC/schema checks where backend resources are touched.

### Phase 6 — Delete Old Wholesale

- [ ] Delete `@angee/sdk`.
- [ ] Delete `@angee/data`.
- [ ] Delete old `@angee/base`.
- [x] Preserve current deletion of app urql/relay/SDL metadata engine; do not
  recreate it during the package split.
- [ ] Verify only the operator daemon quarantine retains urql, and only inside
  `@angee/operator`.
- [ ] Gate: repo-wide `pnpm typecheck/test/build`, backend schema/test gates,
  e2e green, and a reported net line drop.

Addon port order: notes → iam → storage → knowledge → integrate → agents →
parties → messaging → operator console.

## Backend Rider (Parallel Lane)

This folds in Lane L from the researched plan. It can run in parallel with
frontend Phases 1–4 as long as generated artifacts are rebuilt from source.

Implementation order:

1. Land the schema-wide `SnakeNameConverter` / `hasura_config()` seam on
   `AngeeSchema` / schema-bucket build. Then delete `pin_snake_wire_names`, its
   export, and leaked addon call sites. Finish the frontend authored-operation
   cutover in the same rider before calling the slice complete.
2. Make generated Hasura type roles adapter-owned. If `HasuraResource.types` is
   too weak, add a typed role bundle to `strawberry-django-hasura`; do not
   suffix-scan generated type names in Angee.
3. Read group-key aliases, bucket keys, and bucket ranges from
   `strawberry-django-aggregates` / built `<Model>GroupKey` / `BucketRange`
   surfaces instead of recomputing strings.
4. Collapse Django field kind/scalar/widget/measure/to-one checks into one
   classifier that reads the post-composition Strawberry/Hasura surface and
   delegates aggregate operators to the aggregates owner.
5. Default standard `hasura_resource(...)` glue from `(node, model)` so addon
   calls become allowlist declarations, with only true exceptions remaining
   explicit.
6. After those owner folds, split metadata dataclass serialization/merge/
   validation responsibilities; do not polish the duplicate mirror first.

- [x] Finish the schema-wide Hasura snake-case cutover. Backend seam
  (`hasura_config()`) and `pin_snake_wire_names` deletion landed; authored frontend
  GraphQL documents/callers/codegen use snake_case roots; schema/codegen/typecheck
  and full frontend test gates pass.
- [ ] Read generated type roles, group-key/bucket-range/node-prefix/scalar names
  off `HasuraResource` + `strawberry-django-aggregates` owners instead of
  recomputing strings.
- [ ] Collapse the duplicate Django-field classifiers (`scalar`, `measure ops`,
  `widget`, `kind`, to-one relation checks) into one owner reading the
  post-composition Strawberry/Hasura surface; push valid aggregate operators to
  the aggregates owner.
- [x] Emit group bucket drill-down predicates/filters for granular date and JSON
  dimensions from the Hasura/resource metadata owner, so the frontend forwards
  returned `where` instead of reconstructing bucket semantics.
- [ ] Default standard glue (`get_queryset`, aggregate scope, `write_backend`,
  `id_decode`, FK→model map) inside Angee's `hasura_resource(...)`; delete the
  N-copy addon boilerplate.
- [ ] Give backend metadata dataclasses their own serialization and split merge,
  validate, and introspection responsibilities so `metadata.py` stops carrying a
  parallel hand-written artifact mirror.
- [x] Capability metadata exists in `angee.resources` and is consumed by the
  Refine `accessControlProvider`; preserve this through the package split.
- [x] Frontend runtime manifest slice: composer emits package graph/Tailwind/app
  wiring from `AppConfig.angee_web_package`, host CSS imports the generated
  source include, host app imports `composedAddons`/`schemas` from
  `runtime/web/app`, and `@angee/app` owns `angee-web-codegen` instead of
  host-local codegen files.
- [ ] Gate per backend slice: `schema --check`, generated artifact diff review,
  targeted backend tests, and frontend codegen/typecheck where artifacts change.

## Parallelization Boundaries

- **Dialect/codegen lane:** Phase 1 runtime document deletions. Owns
  `@angee/refine` target code and current `@angee/data` dialect glue.
- **Resources/backend lane:** Phase 2 + Backend Rider. Owns resource artifacts,
  classifiers, dimensions, capabilities, and Hasura metadata.
- **UI lane:** Phase 3. Owns rendered primitives, chrome, widgets, feedback, and
  view-mode naming.
- **App/routing lane:** Phase 4. Owns composition, provider mounting, route-tree
  projection, registries, auth/i18n/notification/access control wiring.
- **Addon port lanes:** Phase 5 one addon at a time. A port may not invent local
  primitives; gaps move back to the owning lane.
- **Final deletion lane:** Phase 6 only after all imports point to the new
  packages and gates are green.

## Deletion map (old → new)

| Old | New owner |
|---|---|
| `@angee/sdk` urql engine / relay-invalidation / SDL metadata | **deleted in current branch**; final package split must not recreate it |
| `@angee/sdk` runtime / define-addon / registries | `@angee/app` or tiny contracts module if cycle pressure appears |
| `@angee/data` (provider + string-GraphQL ops + metadata) | `@angee/refine` (provider/dialect) + `@angee/resources` (metadata) |
| `@angee/data` Hasura provider/client/live provider + transport auth | physical `@angee/refine` `provider` + `transport-auth`; old data files/exports deleted |
| `@angee/data` TanStack Router provider bridge | physical `@angee/refine` `router`; old data files/exports deleted |
| `@angee/data` metadata/resource projection/resource type/row contracts | physical `@angee/resources`; old data files/exports deleted |
| `@angee/data` access-control provider/capability mapping | physical `@angee/resources`; old data exports/files deleted |
| `@angee/data` invalidation target/query-metadata glue | physical `@angee/resources` for resource targets + physical `@angee/refine` for authored-query metadata; old data file deleted |
| `@angee/data` stable custom-operation hook dependencies | physical `@angee/refine`; old data file deleted |
| old `@angee/base` (`views/` synonym sprawl, chrome) | `@angee/ui` (canonical primitives + chrome) |
| app urql / graphcache / `relay-*` / `graphql-provider` / `cache-config` | **deleted in current branch**; refine `liveProvider` + react-query |
| operator daemon urql quarantine | keep package-local until rebuilt on a daemon-specific owner; never move back to app/SDK |
| `model-metadata.tsx` SDL introspection | **deleted in current branch**; `@angee/resources/fields` reads the artifact |
| runtime string-built `operations.ts` action documents | **deleted in current branch**; generated per-schema action document registries passed through `createApp`; request/extraction helpers now live in physical `@angee/refine` |
| runtime string-built `operations.ts` aggregate documents | **deleted in current branch**; generated per-schema aggregate registries passed through `createApp`; request/extraction helpers now live in physical `@angee/refine` |
| runtime string-built `operations.ts` delete-preview documents | **deleted in current branch**; generated per-schema delete-preview registries passed through `createApp`; request/extraction helpers now live in physical `@angee/refine` |
| runtime string-built `operations.ts` revision documents | **deleted in current branch**; generated per-schema revision registries passed through `createApp`; request/extraction helpers now live in physical `@angee/refine` |
| runtime string-built `operations.ts` list documents | **deleted in current branch**; stock `@refinedev/hasura` builds list/count documents from `meta.fields`, `filters`, and `sorters` |
| runtime string-built `operations.ts` group documents | **deleted in current branch**; generated per-schema group registries passed through `createApp` |
| runtime string-built `operations.ts` facet documents | **deleted in current branch**; facets run as Refine/TanStack-query native multi-query executions over generated group documents |
| Angee-private public filter dialect / duplicate filter switch layers | Refine `CrudFilters` + one `@angee/refine` Hasura bool-expression serializer |
| typed operation document helpers exported from `@angee/data` / `@angee/sdk` | physical `@angee/refine` `typed-document` + `operation-documents`; no data façade exports |
| duplicate SDK/data i18n interpolation/fallback helpers | i18next provider + one namespace registration/collision seam |
| hand-rolled i18n merge/interpolation/fallback | i18next-native |
| `validateRoutes` string rules | **deleted in current branch**; TanStack Router `getParentRoute` owns nesting |
| menu-trail breadcrumb / menu-derived chrome | **route-static chrome deleted in current branch**; `resource.meta` + `useMenu`/`useBreadcrumb` |
| `pin_snake_wire_names` (+ addon call sites) | schema-wide `hasura_config()` seam plus authored snake_case operation documents; no camelCase aliases |

## Open decisions / risks

- **Composition contracts placement:** the addon manifest type + runtime context +
  registry hooks currently sit between `@angee/ui` and `@angee/app`. Default: put
  the contracts (types + context + `useSlot`/`useWidget` hooks) in `@angee/app` and
  have `@angee/ui` consume them via context whose Provider `@angee/app` mounts — so
  `@angee/ui` does not depend on `@angee/app`. Revisit a tiny `@angee/kit` only if a
  cycle appears.
- **Headless view-state as a package:** kept as `@angee/ui/views/model` for now;
  extract to its own package only if a second renderer ever appears.
- **Cutover window:** old and new packages coexist during Phase 5; they must NOT
  share a cache or live engine (the failure of the last attempt). Each ported addon
  runs fully on the new stack; nothing bridges the two.
- **Runtime GraphQL document generation:** resolved to the generated frontend
  runtime manifest plus the `@angee/app` `angee-web-codegen` CLI. Runtime string
  assembly remains forbidden by P5.
- **Resources DAG cleanup:** physical `@angee/resources` stays below
  `@angee/refine` in the DAG and does not depend on the dialect package. Keep it
  that way as capability/dimension modules are split.

## Definition of done

- Four packages exist with one-way deps; `@angee/sdk`/`@angee/data`/old `@angee/base`
  deleted; net line drop reported.
- No app SDL introspection; no runtime string GraphQL; no second app cache/live
  engine.
- Every generic concern rented (providers wired per "Provider wiring"); the
  bespoke-keep list is the only Angee-owned generic surface, each named + justified.
- One name per concept; the canonical view-primitive set is the public API; addon
  web packages follow the template (documented in `docs/frontend/guidelines.md`).
- Backend rider landed; `schema --check` clean; capability metadata emitted.
- Repo-wide backend/frontend/schema/e2e gates green.
