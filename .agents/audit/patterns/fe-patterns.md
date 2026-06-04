# Frontend pattern-consistency inventory

Read-only audit of `packages/{sdk,base}/src`, `examples/notes-angee/src/{web,example/notes/web}/src`,
and `src/angee/operator/web/src` (tests, stories, `dist`, `__generated__`, `node_modules` skipped).
Scope note: the task named `examples/notes-angee/src/web/**`; the real consumer-addon
views live in `examples/notes-angee/src/example/notes/web/**`, which is included here.

References: `docs/stack.md` (library owners + "branded boundary types"),
`docs/frontend/guidelines.md` (one component tree, shared primitives, slots-before-fork).

---

## 1. Stable-deps / value-equality memo idiom
- canon: `packages/sdk/src/stable-deps.ts` — the one audited home for "memo a
  value by a derived key, suppress exhaustive-deps once". Exports
  `useStableArray` (`.join("")`), `useStableVariables` (`JSON.stringify`),
  `useStableMeasures` (`JSON.stringify`). Its module comment explicitly says the
  lint suppression "lives here, in one audited place, instead of being repeated
  at every call site."
- variant A — private copy of `useStableVariables` inside a data-hook module:
  `packages/sdk/src/authored-hooks.ts:13-19` (1). Byte-for-byte the same body as
  `stable-deps.ts:22-28`, with its own `eslint-disable`. The canon already
  exports this exact function; this is a pure duplicate.
- variant B — inline `JSON.stringify` memo keys at the call site (the thing canon
  exists to remove): `packages/sdk/src/resource-hooks.ts:101-102` (`filterKey`,
  `orderKey` feeding a hand-suppressed `useMemo` at :134-144) and
  `packages/sdk/src/resource-hooks.ts:279` (`fieldsKey = fields.join(" ")`
  feeding the suppressed `useMemo` at :280-287) (2 sites). These re-implement
  `useStableVariables`/`useStableArray` inline instead of calling them.
- variant C — recursive **sorted-key** stable serializer in a view:
  `packages/base/src/views/grouped-list.tsx:959-972` (`stableSerialize`), used as
  a memo/effect key at `:309-316`. Order-independent (sorts object keys), unlike
  every SDK variant which is order-sensitive `JSON.stringify`. Genuinely
  stronger, but a fourth distinct shape with no shared home (1).
- variant D — ad-hoc string-concat memo keys in chrome/views:
  `packages/base/src/chrome/Breadcrumb.tsx:43,116-121` (`trailKey`, `.join("|")`),
  `packages/base/src/views/data-view-surface.ts:349-351` (`groupPathKey` =
  `JSON.stringify(path)`), `packages/base/src/views/data-view-model.ts:374`
  (`serializeDataViewGroupStack`) (3). Each hand-rolls its own key; none reuse a
  shared primitive.
- verdict: DRIFTED
- recommend: one canonical `@angee/sdk` `stable-deps` module is the source of
  truth. Delete the `authored-hooks.ts` private copy (import from `stable-deps`).
  Replace the inline `filterKey`/`orderKey`/`fieldsKey` memos in `resource-hooks`
  with `useStableVariables`/`useStableArray`. Promote a single canonical
  serializer (`grouped-list.tsx`'s sorted-key `stableSerialize` is the most
  correct) into the SDK and have `useStableVariables` use it, so order-equal
  objects compare equal everywhere; then point Breadcrumb/data-view keys at it.

## 2. Data-hook shape
- canon: `@angee/sdk` headless hooks over the shared `useDocumentQuery` read seam
  (`packages/sdk/src/document-query.ts`): `useResourceList`/`useResourceRecord`/
  `useResourceMutation` (`resource-hooks.ts`), `useResourceAggregate`/
  `useResourceGroupBy` (`aggregates.ts`), `useAuthored{Query,Mutation,Subscription}`
  (`authored-hooks.ts`). Uniform `{ data|rows, fetching, error, refetch }`,
  variables stabilized via `stable-deps`, document built by `selection.ts`.
- variant A — operator console authors a **parallel** hook layer that bypasses
  the SDK entirely: `src/angee/operator/web/src/data/transport.tsx`
  — `useOperatorSnapshot` (`useQuery` + manual `setInterval` polling at :232-237,
  `requestPolicy: "cache-and-network"`) and `useOperatorAction`
  (`useMutation` wrapper, :256-276). Hand-written documents in
  `data/documents.ts`, own urql `Client` (`operator-client.ts`), own
  `OperatorTransportProvider`. None of the SDK's read seam, stable-deps,
  refetch-on-change, or error-normalize is reused (whole module, ~8 hooks/helpers).
- variant B — consumer addon mixes canon + bespoke: `NotePage.tsx:205` uses the
  SDK `useAuthoredQuery` (good), but the page also owns local
  `recordId`/`creating` state and a hand-rolled `runDaemonAction`-style flow
  exists only in operator (1).
- variant C — `auth-hooks.ts` (`useRuntimeAuthState`, `useLoginWithPassword`,
  `useLogout`) routes reads through `useDocumentQuery` but its mutations call
  `useUrqlMutation` directly with a `reset()` side effect — a third mutation
  shape distinct from both `useResourceMutation` and `useAuthoredMutation` (1).
- verdict: DRIFTED
- recommend: the operator console is a second GraphQL endpoint (the daemon), so a
  separate urql client/provider is justified — but the *hook ergonomics*
  (`useDocumentQuery` read seam, `useAuthoredMutation` runner, stable variables,
  polling) should be SDK primitives the operator parameterizes by client, not a
  re-implementation. Lift `useOperatorAction` onto `useAuthoredMutation` and add
  a `requestPolicy`/poll option to `useDocumentQuery` so operator reuses it.

## 3. GraphQL field/type NAME derivation
- canon: the backend owns names; the SDK should consume generated names, not
  guess. Generated contracts exist: `packages/sdk/src/__generated__/{public,console}.ts`
  (types) and `resource-types.ts` (the `ResourceTypeMap`).
- variant A — heuristic derivation from the model label (the "compute a GraphQL
  name from outside" smell, in one place but load-bearing everywhere):
  `packages/sdk/src/selection.ts` — `typeNameForModel` (`:92`, `.pop()` +
  first-letter upper-case), `singularFieldName` (`:99`, first-letter lower-case),
  `pluralFieldName`+`pluralize` (`:105-126`, hand-rolled English pluralizer that
  its own comment admits cannot do irregulars), `aggregateFieldName`/
  `groupByFieldName` (`:234,239`, string suffix `Aggregate`/`Groups`). Every
  resource document name (`notes` → `notesConnection`, `NoteFilter`, `noteGroups`)
  is guessed, not read from SDL (1 module, ~6 derivers).
- variant B — fully hand-written operation + field names: operator
  `data/documents.ts` (all root fields/args typed by hand against the daemon SDL),
  `auth-hooks.ts:13-24` (`USER_SELECTION`, `login`, `logout`, `currentUser`
  literal), consumer `NotePage.tsx:19-28` (`NoteRevisions`/`noteRevisions`
  literal). Names are written, not derived (3 surfaces).
- non-issue: `list-internals.tsx:733 pluralize` and `:853 titleCase` derive
  **display labels** (different intent from GraphQL names) — correctly separate,
  leave as-is.
- verdict: DRIFTED (canon is "read names from the contract"; the resource layer
  guesses them)
- recommend: the generated `ResourceTypeMap` already keys by type name — extend
  it (or a sibling generated map) to carry the schema's actual singular/plural/
  aggregate/group field names, and have `selection.ts` look them up instead of
  running `pluralize`/first-letter-casing. This removes the irregular-plural
  failure mode the code already flags.

## 4. Component composition + file/dir naming + index re-export
- canon (per guidelines "shared primitives", "one component tree"): feature
  directories, single-responsibility files, a per-dir `index.ts` barrel
  re-exported from the package root. Most of `@angee/base` follows this:
  `auth/ chrome/ communication/ feedback/ fragments/ i18n/ layouts/ lib/ page/
  shell/ toolbars/ views/ widgets/` all carry `index.ts`.
- variant A — file-naming is three conventions side by side:
  - PascalCase components: `fragments/*.tsx`, `page/*.tsx`, `views/ListView.tsx`,
    `views/FormView.tsx`, `views/DataPage.tsx`, `views/AggregatePanel.tsx`.
  - kebab-case: all of `ui/*.tsx`, plus `views/board-view.tsx`,
    `views/group-list-view.tsx`, `views/grouped-list.tsx`,
    `views/list-internals.tsx`, `views/data-view-*.ts`.
  - camelCase: `widgets/tagInput.tsx`, `widgets/ownerCell.tsx`,
    `widgets/userRef.tsx`, `widgets/statusBadge.tsx`, `widgets/progressBar.tsx`,
    `widgets/scalarText.tsx`, `widgets/themePicker.tsx` — yet
    `widgets/statusbar.tsx` (all-lowercase) and `widgets/markdown-codemirror.ts`
    (kebab) sit in the same dir. `views/` alone mixes PascalCase and kebab-case.
- variant B — barrel inconsistency: every feature dir has `index.ts` **except
  `ui/`** (and `styles/`, which is CSS). `ui/` is instead enumerated
  file-by-file (~45 `export *` lines) in `packages/base/src/index.ts:37-82`,
  while every other dir is one `export * from "./<dir>"`. So `ui` is the only
  primitive group without a local barrel, and the root index owns its surface.
- verdict: DRIFTED (naming), partially CONSISTENT (barrels: one dir is the
  exception)
- recommend: pick one file-naming rule for components (PascalCase for `.tsx`
  components reads cleanest given `fragments/`/`views/` already lean that way);
  rename `widgets/*` and the kebab `views/*` accordingly. Add `ui/index.ts` and
  collapse the 45 root re-export lines to `export * from "./ui"`.

## 5. Data-view abstraction (ListView / GroupListView / BoardView / FormView)
- canon: a single data-bound surface hook, `views/data-view-surface.ts`
  (`useDataViewSurface`), wraps the SDK list hook + TanStack Table and is the
  shared engine for all list-shaped views. `BoardView` (`board-view.tsx`) and
  `GroupedListBody` (`grouped-list.tsx`) are correctly fetch-free renderers fed
  by that surface.
- variant A — `ListView.tsx` vs `group-list-view.tsx` are near-duplicate
  *shells*. Both: resolve `useDataViewMaybe`/`DataViewProvider` (ListView:43-58 ≈
  GroupListView:57-73), call `useDataViewSurface`, build a `toolbarPager` memo,
  `buildFilterOptions`, `activeFilterIdsFor`, `setPage`, `useBulkDelete`, then
  render the identical `<ControlBand><DataToolbar/></ControlBand>` +
  `SelectionBar` + error block + `FlatListBody` + fetching spinner +
  `DeletePreviewDialog` markup (ListView:134-217, GroupListView:198-321).
  GroupListView is a strict superset (adds grouped/board branches + `defaultGroup`)
  (2 components, ~80 duplicated lines).
- variant B — operator console does **not** use this abstraction at all: each
  section (`ServicesSection.tsx`, `WorkspacesSection.tsx`, `SourcesSection.tsx`,
  `OperationsSection.tsx`, `SecretsSection.tsx`, …) hand-builds raw
  `Table/TableHeader/TableRow/TableCell` with its own empty-state row, loading
  (`SectionLoading`) and error (`SectionError`) handling (8 section files). No
  pager, no selection, no filter — a parallel tabular surface.
- variant C — `FormView.tsx` is the sole record-form surface (no duplicate);
  consumed via `DataPage`. CONSISTENT on its own.
- verdict: DRIFTED
- recommend: fold `ListView` into `GroupListView` — the flat list is just the
  grouped view with grouping disabled. Extract the shared shell
  (`ControlBand`+`DataToolbar`+`SelectionBar`+error+spinner+`DeletePreviewDialog`)
  into one `DataViewFrame` both render, or have `DataPage` always mount
  `GroupListView` and gate grouping by props. Operator section tables are a
  different domain (single daemon endpoint, no offset pagination) so a shared
  `DataView` is overkill, but `SectionLoading`/`SectionError`/empty-row are
  repeated per section and want a single `SectionTable` primitive.

## 6. GraphQL types (codegen vs hand-written)
- canon: types are codegen'd from SDL, never hand-maintained; operations stay as
  document strings + urql generics. `packages/sdk/codegen.ts` emits
  `__generated__/{public,console}.ts` from `schema/contract.graphql`;
  `src/angee/operator/web/codegen.ts` emits `__generated__/operator.ts` from the
  daemon-introspected `schema/operator.graphql`. `data/types.ts:1-21` re-exports
  the generated daemon types and explicitly refuses to hand-maintain them.
- variant A — frontend-only composite/result types written by hand on top of the
  generated ones: `operator/data/types.ts:45-80` (`OperatorSnapshot`,
  `OperatorSnapshotQueryData`, `OperatorSnapshotSections`) — justified, the daemon
  has no single snapshot object, and they're built from generated members (1).
- variant B — result/variable interfaces declared inline at the call site instead
  of codegen: `NotePage.tsx:30-43` (`NoteRevision`, `NoteRevisionsData`,
  `NoteRevisionsVariables`) — the consumer addon hand-types an authored query's
  result rather than generating it (1). Minor and idiomatic for the escape-hatch
  hook, but it is hand-written domain type drift.
- verdict: CONSISTENT (the codegen-from-SDL discipline holds on both surfaces;
  the daemon snapshot composites are a sanctioned, documented exception)
- recommend: leave as-is. Optionally note in guidelines that authored-query
  result types (like `NoteRevision`) are the one sanctioned place for
  hand-written GraphQL result shapes, so it doesn't read as drift.

## 7. Type patterns (branded boundary types, `any` / `as unknown as` drift)
- canon (stack.md): "TypeScript … Angee adds **branded boundary types**." Escape
  hatches (`any`, `as unknown as`) are forbidden.
- finding — escape hatches: **zero** `as unknown as`, `: any`, `as any`, `<any>`,
  or `any[]` across all scanned source. Clean (0). Good.
- variant A — branded boundary types **do not exist**. IDs at every boundary are
  plain `string`: `resource-hooks.ts` `id: string | null`, `ResourceMutationVariables.id?: string`,
  `resource-result.ts` `objectId: string | null`, `auth-hooks.ts` user id,
  `OperatorConnectionInfo.token: string`. No `Brand`/`__brand`/`unique symbol`
  anywhere. The one "Brand" mention (`define-addon.ts:67`) is a factory marker,
  not a TS branded type. So the stack.md promise is unmet (0 brands).
- variant B — `Record<string, unknown>` as the untyped-boundary fallback is
  pervasive and used two ways: (a) legitimately at parse/extract boundaries that
  narrow immediately (`resource-result.ts`, `aggregate-extract.ts`,
  `transport.tsx` `parseOperatorConnection`); (b) as a permanent payload type that
  is never narrowed — `run-action.ts:2` `DaemonActionData = Record<string, unknown>`
  then indexed by a string `field` passed from outside
  (`runDaemonAction` `data[field] == null`), and `ServiceActionVars extends
  Record<string, unknown>`. Use (b) is the constitution's "function inspects an
  object by a passed-in key to decide something" smell (~6 sites in operator
  sections + run-action).
- verdict: DRIFTED (branded types absent despite stack.md; `Record<string,unknown>`
  used as a typed-payload substitute in operator)
- recommend: either implement branded boundary types (a `RelayId`/`Sqid` brand
  used by `useResourceRecord`, mutation ids, `objectId`, and the operator token)
  or strike the "branded boundary types" claim from stack.md so code and doc
  agree (a doc/code mismatch is, per the constitution, a bug). For operator,
  replace `DaemonActionData = Record<string,unknown>` + `data[field]` success
  probe with the generated `MutationResult`/payload types so success is checked
  by a typed field, not a string key.

---

## Top inconsistencies (worst first)
1. **Operator console is a whole parallel data layer** (Patterns 2, 5, 6, 7):
   its own urql client, provider, hand-written documents, `useOperatorSnapshot`/
   `useOperatorAction` hooks, raw `Table` rendering, and `DaemonActionData =
   Record<string,unknown>` keyed by a passed `field` string. None of the SDK read
   seam / authored-mutation runner / data-view surface is reused. A second
   endpoint warrants a second client, not a second hook+view vocabulary.
2. **Stable-deps idiom forked into 4 shapes** (Pattern 1): the audited
   `stable-deps.ts` canon, a byte-identical private copy in `authored-hooks.ts`,
   inline `JSON.stringify`/`join` keys in `resource-hooks.ts`, and a stronger
   sorted-key `stableSerialize` in `grouped-list.tsx` — the exact duplication the
   canon module's comment says it exists to prevent.
3. **`ListView` vs `GroupListView` near-duplicate shells + the branded-type gap**
   (Patterns 5, 7): the two list components share ~80 lines of identical
   toolbar/selection/error/dialog markup (GroupListView is a strict superset of
   ListView), and stack.md's promised "branded boundary types" are entirely
   absent — every id crosses the boundary as a bare `string`, so the doc and code
   disagree.

## 5-line summary
- DRIFTED: Patterns 1 (stable-deps, 4 shapes), 2 (data-hooks — operator parallel
  layer), 3 (GraphQL names guessed via `pluralize`/casing in `selection.ts`),
  4 (file naming Pascal/kebab/camel + `ui/` lacks a barrel), 5 (ListView≈
  GroupListView; operator hand-rolls tables), 7 (no branded types; `Record<
  string,unknown>` as typed-payload).
- CONSISTENT: Pattern 6 (codegen-from-SDL holds on both schemas + the daemon);
  and zero `any`/`as unknown as` escape hatches anywhere.
- Top 3: (1) operator console is a parallel data+view layer that reuses none of
  the SDK hook/surface vocabulary; (2) the stable-deps memo idiom forked into
  four implementations despite an explicit single-home comment; (3) ListView/
  GroupListView duplicate their shell, and the stack.md "branded boundary types"
  contract is unmet (all ids are bare `string`).
