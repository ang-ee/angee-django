# Refine Adoption & SDK Decomposition — Design

Date: 2026-06-23
Status: Decisions set. **Dialect = Hasura (2026-06-23, A/B spike) — supersedes the
nestjs-query choice this doc discusses.** Where this doc says "nestjs-query" /
`@refinedev/nestjs-query` / `strawberry-django-nestjs`, read the **Hasura**
equivalent (`@refinedev/hasura` / `strawberry-django-hasura`); the data-provider
abstraction, rebac-owned authorization, and the SDK decomposition all hold
unchanged — only the emitted dialect differs (aggregates now **free**, sqid via
`idType:"String"`). The current execution truth is
`refine-adoption-refactor-plan.md` (greenfield, Hasura).

Related (read these; this doc does not duplicate them):

- `.agents/notes/data-management-library-research.md` — the backend filter/order/
  aggregate **owners** (`strawberry-django`, `strawberry-django-aggregates`).
  This design **defers all aggregate/filter mechanics to that note**; do not
  reimplement them here.
- `.agents/plans/data-management-odoo-parity.md`,
  `.agents/plans/view-composition-drift-audit.md` — existing data-view work the
  decomposition must preserve.
- `.agents/plans/library-leverage-research-checklist.md` — the "delete Angee code
  by using the library's native shape" discipline this design applies.
- `docs/stack.md` — stack rows changed by this design (see §9).
- `packages/sdk` — the package being decomposed.

## 1. Problem

`@angee/sdk` is, concern-for-concern, a hand-rolled re-implementation of
[refine.dev](https://refine.dev): data hooks (`useResourceList/Record/Mutation` ≈
`useList/useOne/useCreate/useUpdate/useDelete`), bespoke reads (`authored-hooks` ≈
`useCustom`), auth (`auth-*` ≈ `authProvider`), live invalidation
(`relay-invalidation` ≈ `liveProvider`), i18n (`i18n` ≈ `i18nProvider`), table
state (`data/view-state` ≈ `useTable`), and a resource/addon runtime
(`runtime`/`define-addon` ≈ `<Refine resources>`).

An independent architecture review (2026-06-23) found:

- The **shared hook core is genuinely well-factored** — every read routes through
  one `useDocumentQuery`, every write through one `useDocumentMutation`, refetch
  through one `relay-invalidation`. The "parallel reinvented hooks" hypothesis is
  largely false.
- The **two genuinely reinvented wheels** are: (a) `selection.ts`, which builds
  GraphQL by **string concatenation** with a hand-rolled injection-guard regex;
  (b) `data/view-state.ts`, whose sort/filter/paginate state **overlaps TanStack
  Table**, already locked.
- The **real defects** (shape-decoding `if`-chains, two-sources-for-one-fact root
  field **inference**) live in the **Angee-specific metadata/filter layer** —
  exactly the part refine does **not** own.

Conclusion: stop maintaining the generic half (refine does it as well or better);
keep and **clean** only the Angee-specific moat; let an established stack own
everything generic. Goal: **least hand-rolled code on top of what libraries
already provide.**

## 2. Decisions (set by architect, 2026-06-23)

1. **Adopt refine.** The refine↔backend translation lives in a dedicated adapter,
   **not** in the forks (a source read confirmed reshaping the forks = substantial
   rewrites). See §6 for where: recommended is a standalone backend library
   `strawberry-django-nestjs` → **stock `@refinedev/nestjs-query`** on the
   frontend; the lighter alternative is a thin frontend dataProvider on
   `@refinedev/graphql`.
2. **Delete `selection.ts`.** No runtime document assembly. Views use
   **codegen-authored `TypedDocumentNode` documents** passed via `meta.gqlQuery`
   (GraphQL Code Generator client-preset, already in the stack).
3. **Forms: TanStack Form → react-hook-form** + `@refinedev/react-hook-form`. The
   declarative `<Field>`/`<Group>` DSL and `FormView` ergonomics are preserved on
   top; only the state engine changes.
4. **Keep metadata-driven UI**, but source it from an **authoritative backend
   metadata artifact**, never frontend introspection/inference. This deletes the
   review's flagged heuristic root-field inference.
5. **GraphQL type names drop the `Type` suffix** — `Note`, `NoteConnection`, … (not
   `NoteType`/`NoteTypeConnection`). Matches nestjs entity naming and architect
   preference; a codebase-wide rename of Angee's `<Model>Type` convention (every
   `@strawberry_django.type` class + `data_query` type names + frontend codegen).

The stack lock is explicitly open to change to serve decisions 1–4 (architect:
"we can change the stack… no problem").

## 3. Target architecture

```
BACKEND (Python) — composer emits a refine-consumable SDL + a metadata artifact
  angee.compose / angee.graphql
    ▸ per model: offset-connection CRUD ( <model>s → { totalCount results pageInfo } )
      filters/order/aggregates: OWNED by strawberry-django + strawberry-django-aggregates
      (see data-management-library-research.md — DO NOT reimplement)
    ▸ <model>Changed subscription            → refine liveProvider
    ▸ ONE authoritative resource-metadata artifact:
      root↔model map, field kind/widget/filter, relation labels, group axes,
      measures, defaults  → feeds refine `resources` AND base field/widget resolution
      (replaces frontend introspection/inference — decision 4)

FRONTEND (TS) — refine + base + two thin Angee packages
  @refinedev/core + @refinedev/nestjs-query + @refinedev/react-table
    + @refinedev/react-hook-form + TanStack Router/Table/Virtual
    + graphql-ws + GraphQL Codegen (TypedDocumentNode) + i18next + zod
        ▲  thin Angee glue (the ONLY substantial hand-rolled layer)
        │
  @angee/data     ▸ dataProvider config + liveProvider(graphql-ws)
                  ▸ useAggregate / useGroupBy / useFacets / useDeletePreview  (non-CRUD hooks)
                  ▸ resource-metadata loader → refine `resources` + field/widget resolution
  @angee/runtime  ▸ defineAddon / composeAddons → refine `resources`
                    + widget/slot/preview/form/icon registries
  @angee/base     ▸ Base UI + Tailwind; binds refine headless hooks into
                    ListView/FormView/DataPage/RecordView;
                    owns board/grouped/favorites view state
  consumer addons ▸ defineBaseAddon; codegen-typed documents via meta.gqlQuery
```

`@angee/base` stays **Base UI** (`@base-ui/react`) + Tailwind — **not** MUI;
refine is used **headless** (`@refinedev/core` + `@refinedev/react-table`), never
`@refinedev/mui`.

## 4. Frontend package decomposition

`@angee/sdk` (31 modules) dissolves into:

### `@angee/data` — the data binding (the irreducible adapter)
- `dataProvider` configured for the Angee endpoint (nestjs-query base; see §6).
- `liveProvider` via `createLiveProvider(graphqlWsClient)` driven by
  `<model>Changed` subscriptions; `liveMode: "auto"`.
- The **~4 non-CRUD hooks** refine has no native equivalent for —
  `useAggregate` / `useGroupBy` / `useFacets` / `useDeletePreview` — over
  `useCustom` + `meta.gqlQuery`. Their internals reuse today's
  `aggregate-extract` + `facets` extraction. Aggregate/group/filter **shape** is
  the backend owner's (defer to the research note).
- The **resource-metadata loader**: reads the backend metadata artifact, feeds
  refine `resources` and base's field→widget/filter resolution. This is the
  decision-4 moat, kept thin.

### `@angee/runtime` — addon composition (the meta-framework concern)
- `defineAddon` / `composeAddons` → merge addon contributions into refine
  `resources` + widget/slot/preview/form/icon registries. ~1–2 files.
- Menus / routes-by-model / breadcrumbs now come from refine `resources` +
  `useMenu`/`useResource`, not a bespoke `runtime.ts` registry.

### `@angee/base` — render layer (unchanged design system)
- Binds `@refinedev/react-table` (TanStack Table) + `@refinedev/react-hook-form`
  + refine core hooks into `ListView`/`FormView`/`DataPage`/`RecordView`.
- Owns the **board / grouped / favorites** view-state slice that has no refine/
  TanStack owner (the rest of `data/view-state` is deleted in favor of `useTable`).

### consumer addons — product code
- `defineBaseAddon`; codegen-typed documents; no hand-rolled hooks.

## 5. Backend changes (minimal, owned in the composer)

1. **Connection shape**: ensure each model's list root exposes the offset
   connection refine reads (`totalCount` + `results` + page info). Likely already
   true; verify per model.
2. **Metadata artifact**: emit one authoritative per-model contract — root↔model,
   field kind/widget/filter, relation identity vs label axes, group axes,
   measures, defaults — at build time (composer) or via a stable metadata query.
   This is the single source for decision 4 and for refine `resources`. The
   research note already calls for exactly this ("Generate or expose frontend
   data metadata from that same contract").
3. **Do NOT** reimplement or relocate filter/order/aggregate mechanics — they are
   owned by `strawberry-django` and `strawberry-django-aggregates` (research
   note). Backend work here is **emit/shape + metadata**, not new data logic.

## 6. RESOLVED — keep the backend DSL; translate in the dataProvider

Grounded by a source read of the owned forks (`../strawberry-django`,
`../strawberry-django-aggregates`, `../strawberry`, `angee/graphql`), 2026-06-23.

Conforming the SDL to nestjs-query's exact shape is *possible* (we own every
generator) but is **substantial rewrites of our own libraries, not config**:

- **Filter** — the lookup vocabulary (`exact/iExact/contains/iContains/inList/
  isNull/range/regex/gt…` + self-referential `AND/OR/NOT`) is baked into
  `FilterLookup` in `strawberry_django/filters.py`. The branch's
  `input-object-extensions` only *adds* fields; it cannot *replace* the shape.
  nestjs naming (`eq/iLike/in/is`) ⇒ fork/rewrite `FilterLookup` and redirect
  every `filter_type()`. **Hardest.**
- **Aggregate/groupBy** — flat `count/sum_<f>/avg_<f>…` + `GroupedResult
  {results,pageInfo,totalCount}` + `GroupBySpec`/`Having`/bucketing are generated
  by `make_aggregate_type()` et al. in `strawberry-django-aggregates`. nestjs's
  `<Entity>AggregateResponse{groupBy,count{…},sum{…}}` nesting ⇒ rewrite those
  generators. **Hardest** — and our engine (having, temporal bucketing, filter
  echo, cursor option) is *richer* than nestjs-query's.
- **Connection** — `OffsetPaginated{results,total_count,page_info}` is
  strawberry-django-owned; `results→nodes` needs a wrapper. **Hard.**
- **Argument names** (`filter`/`order`/`pagination`/`group_by`) are hardcoded in
  the resolvers. **Hard** to rename.
- **Order** — `{field: Ordering}` `@oneOf` map today, **but** the aggregate path
  already emits the nestjs-style `list[{field,direction,nulls}]`
  (`make_group_order_input`). **Medium** — the one place conformance is cheap.

Root field/arg *naming* is fully Angee-controlled (`data_query(list_name=…)`,
`crud(name=…)`); the *shapes* are library-owned.

The irreducible refine↔backend translation must live *somewhere*. Three places:

- **A — modify the forks.** Bake the nestjs shape into `strawberry-django` /
  `strawberry-django-aggregates`. **Rejected**: diverges the forks from upstream
  forever and is copied into every backend consumer.
- **B — frontend mapping module.** A thin dataProvider on `@refinedev/graphql`
  maps refine ⇄ strawberry shape. *Lighter* (one TS module) but the glue is
  Angee-frontend-specific and not reusable; the frontend is not stock.
- **C — a standalone backend adapter library `strawberry-django-nestjs`
  (RECOMMENDED).** A NEW package that **composes** (does not modify) Django ORM +
  `strawberry-django` + `strawberry-django-aggregates` and emits the nestjs-query
  GraphQL contract. The frontend then uses **stock `@refinedev/nestjs-query`** —
  zero frontend mapping glue.

C is the clean realization of "separate DSL *shape* from *semantics*": the new lib
owns the nestjs *shape*; Django ORM + strawberry-django(-aggregates) keep owning
the *execution*. It does not touch upstream (no fork divergence), makes the
frontend fully stock (maximal "least frontend code"), and is a reusable,
publishable library — **symmetric with how Angee already owns/publishes
`strawberry-django-aggregates`** (a sibling to `strawberry-django`, not a fork of
it). No existing Python/strawberry library implements the nestjs-query contract
(verified) — greenfield but single-purpose and bounded.

Per-surface feasibility (each composes an existing owner):

- **Filter** → generate the nestjs `Filter` input; translate ops to Django `Q`
  (`eq→exact`, `neq→~exact`, `in→in`, `like→contains`, `iLike→icontains`,
  `gt→gt`, `is:null→isnull`, `and/or` arrays). Bounded mapping.
- **Sorting** → `[{field,direction,nulls}]` → `.order_by()` (nulls via
  `F(...).asc(nulls_last=…)`). Trivial.
- **Paging/connection** → `{limit,offset}` → queryset slice; emit
  `{nodes,totalCount,pageInfo}` (or cursor `edges`). Trivial.
- **Aggregate** → `<entity>Aggregate(filter): [AggregateResponse{groupBy,
  count{…},sum{…},avg,min,max}]`; compose `compute_aggregation` (a pure queryset
  primitive) and reshape its flat `sum_<field>` output into the nestjs nested
  `sum:{<field>}` shape. **The one nontrivial surface — pin it in the POC.**
- **Identity** → map public `sqid` ⇄ pk at the boundary (compose `SqidField`).

**Two ownership classes (this scopes "do not modify upstream"):**
- `strawberry` + `strawberry-django` are **forks of upstream** projects →
  modifying them carries perpetual merge cost → **compose, don't modify** (the
  adapter generates the nestjs filter/sort/pagination/connection shapes on top).
- `strawberry-django-aggregates` is **Angee's own library with no external
  consumers** → free to modify if needed. **POC finding (2026-06-23): not needed.**
  The adapter composes its `compute_aggregation` primitive (+ public
  `aggregate_alias`/`group_by_alias` helpers) and reshapes the flat rows into the
  nestjs `AggregateResponse{groupBy,count{…},sum{…}}` shape. That shape is thin and
  nestjs-specific (envelope naming, selection-driven groupBy), so it lives in the
  **adapter**, not the shared aggregates library. The only genuine upstream gap is a
  **per-field COUNT op** (`Count(field)` → `count_<field>`) for nestjs's per-field
  non-null `count{field}`; until then `count{field}` is the group-row count
  (faithful for non-null columns).

So the build splits cleanly: `strawberry-django-nestjs` (new) owns the **entire**
nestjs shape — list/CRUD/filter/sort/connection **and** aggregate — composing the
strawberry-django fork and `strawberry-django-aggregates`'s `compute_aggregation`
(neither modified); the frontend uses stock `@refinedev/nestjs-query`.

**Decision: C** (recommended — stock frontend, reusable, no fork divergence);
**B** only to minimize total LOC. **A (modifying the strawberry forks) is out.**
Compose the forked DSL; freely evolve our own `strawberry-django-aggregates`.
Matches `data-management-library-research.md` — "stay glue over the libraries,
not a replacement."

## 7. Migration mapping

| Today (`@angee/sdk`) | Greenfield | Disposition |
|---|---|---|
| `resource-hooks`, `authored-hooks`, `action-hooks` | refine `useList/useOne/use{Create,Update,Delete}` / `useCustom(Mutation)` | **delete** |
| `document-*`, `stable-deps`, `graphql-client`, `graphql-provider`, `cache-config` | `@refinedev/core` + react-query + provider | **delete** |
| `relay-invalidation`, `relay-registry` | `createLiveProvider` + refine `invalidate` | **delete** |
| `selection.ts` | codegen `TypedDocumentNode` via `meta.gqlQuery` | **delete** (decision 2) |
| `data/view-state` sort/filter/paginate | `@refinedev/react-table` | **delete** |
| `data/view-state` board/grouped/favorites | `@angee/base` view state | **move to base** |
| `model-metadata` heuristic root-field inference | backend metadata artifact | **delete** (decision 4) |
| `model-metadata` field→widget/filter classification | `@angee/data` (from metadata artifact) | **keep, thin** |
| `aggregate-extract`, `facets`, cascade delete | `@angee/data` non-CRUD hooks | **keep** |
| `define-addon`, `runtime` | `@angee/runtime` (thinner; menus/routes → refine) | **keep, thinner** |
| `auth-*`, `i18n`, `preferences` | refine `authProvider` / `i18nProvider` | **delete** |

## 8. Glue budget (the "least hand-rolled code" metric)

After migration the total bespoke **frontend** surface is:

- `@angee/data` — provider config + **4 custom hooks** + metadata loader.
  Aggregation is the only genuinely bespoke data *logic* left; the rest is config.
- `@angee/runtime` — `composeAddons` + registries (~1–2 files).
- `@angee/base` — the design system (product value, not glue).

Backend bespoke surface: connection-shape emit + the metadata artifact, **owned
once in the composer** (not copied into consumers). Everything else is rented
from refine + TanStack + Codegen + i18next + graphql-ws. Net **gain**:
access-control, audit-log, notifications, optimistic/undo, and devtools that the
SDK does not have today.

## 9. `docs/stack.md` changes (do in the same change as code)

- **Add** rows: `@refinedev/core` (data/auth/access/audit/notify/live/i18n/
  resource registry), `@refinedev/nestjs-query` (or `@refinedev/graphql` —
  per §6), `@refinedev/react-table`, `@refinedev/react-hook-form`.
- **Change** `urql` row: from "GraphQL client, normalized cache, subscriptions"
  → "transport executor inside the refine dataProvider"; **react-query** (via
  refine) now owns cache/invalidation. (Or replace urql with graphql-request if
  the POC shows no need for urql extras.)
- **Swap** TanStack Form → react-hook-form (decision 3); valibot → **zod** for
  form resolvers (broadest refine/ecosystem support) — confirm or keep valibot.
- Manifests (`package.json`, `pnpm-lock.yaml`) updated in the same change.

## 10. Risks

- **Cache model change**: normalized graphcache → react-query document cache.
  Writes need explicit invalidation; refine `invalidate` + liveProvider cover
  most. Behavior-equivalent but different mental model.
- **Form migration**: re-binding `FormView`'s declarative DSL onto
  react-hook-form (`zodResolver`); the DSL stays, the engine changes.
- **Router**: refine ships no official TanStack Router binding; write a ~4-method
  `routerProvider` (`go`/`back`/`parse`/`Link`). Small, but bespoke.
- **§6 cost pivot** unresolved until the POC.

## 11. Next step — POC (validates the whole bet)

In an isolated worktree, one model end-to-end:

1. Stand up a minimal **`strawberry-django-nestjs`** adapter (option C) over ONE
   `examples/notes-angee` model — filter + sorting + offset connection + one
   aggregate — and point **stock `@refinedev/nestjs-query`** at it.
2. `@refinedev/react-table` list rendered by `@angee/base` `ListView`.
3. One aggregate/groupBy through the stock provider (validates the aggregate
   reshape — the one nontrivial surface).
4. One `FormView` on `@refinedev/react-hook-form` + `zodResolver`.

Exit criteria: confirm (a) the adapter surface size + the aggregate-reshape
fidelity, (b) that the stock provider needs no patching, (c) the form DSL
re-binding effort, (d) live invalidation via `<model>Changed`.

### POC results (2026-06-23) — worktree `poc/strawberry-django-nestjs`

Built `strawberry-django-nestjs` (comparisons / filtering→`Q` / sorting /
connection / paging / mutations) composing strawberry-django, **no upstream
edits**:

- ✅ **CRUD contract + runtime** (`verify.py`): emitted SDL matches the stock
  contract (`Note`/`NoteConnection`, `iLike/neq/notIn/is`, `[NoteSort!]`,
  `OffsetPaging`, `createOne/updateOne/deleteOne`); list(filter+sort+paging)/
  getOne/create/update/delete all execute against SQLite.
- ✅ **Stock client end-to-end** (`server.py` + `poc-client/client_test.cjs`):
  the **unmodified** `@refinedev/nestjs-query@2.0.1` provider drives the schema
  over HTTP — getList/getOne/create/update/deleteOne all pass, **no provider
  patching**. Confirms exit criterion (b).
- ✅ **Aggregate surface** (`verify_aggregate.py`): `noteAggregate(filter):
  [NoteAggregateResponse!]!` with nested `count/sum/avg/min/max` + selection-driven
  `groupBy`, built by composing `compute_aggregation` (**no upstream edits**).
  Grouped published→count 2/sum 30/avg 15/min 10/max 20, draft→count 1; ungrouped
  count 3/sum 60; filter-narrowing — all pass.
- Gotcha: nestjs-query's *ESM* build mis-imports `lodash/set` (missing `.js`);
  use the CJS build (a bundler resolves it in a real frontend).

Remaining: real Angee integration (REBAC qs, schema buckets, `manage.py schema`),
the React + `@refinedev/react-table` visual render (the provider layer beneath it
is proven), and (optional, upstream) a per-field `Count(field)` op in
`strawberry-django-aggregates`.

## 12. Out of scope / deferred

- Aggregate/filter **backend mechanics** — owned by the research note.
- Full addon migration — sequenced after the POC and the base bindings land.
- The eventual `django-angee` / `django-angee-addons` package split is unaffected.

## 13. Multi-backend reuse — the operator daemon on the same client

The nestjs-query contract (`CONTRACT.md`) is a **cross-backend** spec, not a
Django-only one. refine supports multiple **named data providers**
(`dataProvider={{ default, operator }}`), all driven by the same stock
`@refinedev/nestjs-query`. So if the **angee-operator** GraphQL daemon (Go) also
emits the nestjs contract, the operator console reuses the *same* provider +
frontend stack as the Django console — **one client, two backends**.

- The shared asset is the **contract**, not the Python adapter: the Go daemon
  emits the nestjs shape Go-side; `strawberry-django-nestjs` emits it for Django.
  `CONTRACT.md` becomes the single source both backends target.
- Replaces the operator's current bespoke daemon shape (e.g.
  `MutationResult{status}`) and its separate hand-maintained client/codegen
  (`docs/frontend/guidelines.md` Pitfalls).
- Separate workstream (Go daemon refactor); out of scope for this Python POC, but
  the contract is intentionally backend-neutral so the operator can adopt it.
