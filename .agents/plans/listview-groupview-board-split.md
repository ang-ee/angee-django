# Split the data-view list into ListView / GroupListView / BoardView

**Goal (architect, 2026-06-02):** A page, when it declares its views, opts for a
lean **`ListView`** (flat list — no grouping/aggregation machinery pulled in) or
a **`GroupListView`** (folded server-driven groups + two pagers + nested + per-group
aggregates), and likewise a **`BoardView`** (Kanban). *"Simple apps don't need to
lift aggregation filtering all that."* So the grouping/aggregation code (`useResourceGroupBy`,
`grouped-list.tsx`, the group-by toolbar controls) must live ONLY in `GroupListView`,
never in the lean `ListView` — code separation, not just visual. `GroupListView`/
`BoardView` share `ListView`'s base via **composition** (React's idiomatic "inherit").

## Current state (already decomposed somewhat)
- `views/ListView.tsx` (~1150 lines): the monolith — orchestration (data query,
  toolbar, columns, selection, sort/filter/pagination, data-view state) + flat
  virtualized table + board (`BoardRows`) + dispatches to `GroupedListBody` when grouped.
- `views/grouped-list.tsx`: `GroupedListBody`/`GroupSection` (folded, two pagers,
  nested recursion) — the grouped body. ✅ already its own module.
- `views/list-internals.tsx`: shared leaf primitives (RecordRow, cellContent,
  buildColumns, group key/label helpers). ✅
- `views/DataPage.tsx`: renders `<ListView>` + the view-switcher; pages pass
  `defaultGroup` (the notes page sets `{updatedAt, day}` → grouped by default).

## Proposed structure (composition; lean ListView + extensions)
1. **Shared base** — extract the orchestration every view needs into a hook
   `useDataViewSurface(model, {columns, fields, filter, order, pageSize})` (new module,
   e.g. `views/data-view-surface.ts`), returning: the resolved columns/`tableColumns`,
   selection helpers, sort/filter/pagination from the data-view context, the requested
   fields, the merged filter, and the toolbar inputs. **It uses `useResourceList` only —
   NOT `useResourceGroupBy`.** The flat **table render** (the `<Table>` + virtualized
   rows + select-all) can be a shared `FlatListBody` in `list-internals` reused by ListView
   and as the no-group fallback of GroupListView.
2. **`ListView` (lean)** = `useDataViewSurface` + `FlatListBody` + a toolbar WITHOUT
   group-by controls. NO import of `grouped-list`/`useResourceGroupBy`/board. This is
   what a simple page picks.
3. **`GroupListView`** = `useDataViewSurface` + the grouping layer: `useResourceGroupBy`,
   `GroupedListBody` (folded/nested), the group pager, and a toolbar WITH group-by. When
   no group is active it renders `FlatListBody` (so it's a strict superset of ListView).
   The `defaultGroup` prop lives here.
4. **`BoardView` (Kanban)** = `useDataViewSurface` (data/columns) + the lane rendering
   (extract `BoardRows`/`BoardLane`/`BoardCardShell` from ListView into `views/board-view.tsx`).
   Lanes need a group axis → toolbar with group-by.
5. **Page opt-in (DataPage)** — DataPage lets the page choose the list view component.
   Candidate APIs (pick in review): (a) a `listView?: "list" | "group"` prop (default
   `"list"`; notes passes `"group"`); (b) pass the component itself; (c) a `views` array
   declaring which view kinds the page supports (drives the switcher). The view-switcher
   then toggles among the page's declared views; `defaultGroup` only applies to GroupListView/BoardView.

## Constraints / invariants
- **ListView must not transitively import the grouping/aggregation code** (verify: no
  `useResourceGroupBy`/`grouped-list` in ListView's import graph). This is the whole point.
- Behavior preserved: the notes list (opts into GroupListView) renders + behaves exactly
  as today (folded groups, two pagers, nested, board lanes). e2e 41/0 + base 35 must stay green.
- Freeze guards (`autoResetPageIndex:false`), selection, column chooser, sort cycle, the
  Pager primitive, and the data-view (Router-search) state are unchanged — just relocated.
- DRY: one owner for the orchestration (the surface hook), one for the flat body, one for
  the grouped body, one for lanes. No duplicated cell/row/column/selection logic.

## Open design questions (for plan-review)
- **Q1 — shared base: hook vs base component?** Lean toward a `useDataViewSurface` HOOK
  (logic) + small shared body components (`FlatListBody`), so each view composes without a
  wrapper. Confirm this is the right seam vs a `<DataViewShell renderBody>` component.
- **Q2 — DataPage opt-in API.** Which of (a)/(b)/(c) above? A `listView: "list"|"group"`
  string prop is the least churn + most discoverable; a `views` array is more flexible
  (declares the switcher set). Which fits the framework's declare-at-build-time style?
- **Q3 — the view-switcher + grouped.** Today the switcher toggles `list`/`board` (runtime
  data-view state) and grouping is a runtime mode. After the split, is "grouped" still a
  runtime mode of GroupListView (group active), with the switcher only toggling
  ListView/GroupListView↔BoardView? Or does grouping become a declared view? (Recommend:
  grouped stays a runtime mode of GroupListView; the switcher toggles the page's declared
  view components.)
- **Q4 — where the surface hook + flat body live** (list-internals vs a new
  data-view-surface module) to keep the dependency graph clean (ListView must not pull grouping).

## Resolved design (post plan-review, 2026-06-02)
- **Q5 (the crux) — group STATE stays one shared owner.** The view-state model
  (filter/sort/page + the `group`/`then` slot) and `DataViewProvider` stay shared;
  splitting them would duplicate the URL codec + the freeze-critical reducer. The
  **lean invariant is scoped to CODE**: ListView's import graph must exclude
  `grouped-list.tsx`, `useResourceGroupBy`, and `AggregatePanel`. A few optional URL
  keys are not "aggregation machinery." The build-order import-graph check targets
  exactly that set.
- **Board fix.** Board lanes are built from **client-side `groupRows`** over the page
  rows (`BoardRows`/`BoardLane`/`BoardCardShell`), NOT `useResourceGroupBy`. The
  current board-mode `useResourceGroupBy` + `groupCounts` is **dead wiring** (feeds
  `GroupHeader`, which never renders in board mode) — DELETE it.
- **Q1 — surface = a HOOK.** `useDataViewSurface` owns: the `useResourceList` query,
  the single TanStack table instance + `getRowId` + the freeze guards
  (`autoResetPageIndex/Expanded:false`), `columnVisibility` + `visibleFields`/toggle,
  selection + page-select-all, the row virtualizer, `tableColumns`, `requestedFields`,
  `mergedFilter`. It does NOT own `groupOptions`, the grouped `toolbarPager` branch, or
  `pagerSubject="Groups"` — those are GroupListView's. The freeze-guard docstring MOVES
  with the table (one owner, not duplicated).
- **`FlatListBody`** is a pure presentational consumer of the surface's table+virtualizer
  (in `list-internals` only if it stays a leaf with no extra hooks). ListView renders it;
  GroupListView renders it as its no-group fallback. GroupListView's per-leaf tables stay
  in `grouped-list.tsx`.
- **Q2 — DataPage opt-in = a component prop**, not a `"list"|"group"` enum (avoid
  switch-on-type; "extend, don't fork"). `DataPage` takes `list?: ComponentType<ListViewProps>`
  defaulting to the lean `ListView`; the notes page passes `GroupListView`. Confirm the
  chosen component still satisfies the `onListStateChange` contract the `ListStateProbe` /
  record-pager depend on (consider having the probe reuse the surface hook to drop a 3rd
  copy of the list query).
- **Q3 — board stays a runtime `view` mode** of a board-capable shell (keep
  `DATA_VIEW_KINDS`/`DataViewSwitcher`). The **lean ListView renders no switcher and
  ignores `view`**; `?view=board` degrades to the list on a lean-only page.
- **Q4 — `useDataViewSurface` lives in `views/data-view-surface.ts`** (never
  `list-internals`, which is leaf-only). Update the layering docstrings.

## Decomposition (bounded slices, not one big build)
- **Slice 1 (foundation, behavior-preserving):** extract `useDataViewSurface` +
  `FlatListBody`; refactor the CURRENT ListView to compose them; DELETE the dead
  board-mode `useResourceGroupBy`/`groupCounts` block. No new views yet, no API change.
  Verify e2e 41/0 + base 35 (pure refactor).
- **Slice 2 (the split):** lean `ListView` (no grouping imports) + `GroupListView`
  (grouping, default-group, group toolbar) + `BoardView` (lanes); a dispatcher picks the
  runtime view. Verify ListView's import graph is grouping-free.
- **Slice 3 (consumer API):** `DataPage` `list` component prop (default lean ListView);
  notes page opts into `GroupListView`. e2e + a lean-page-has-no-grouping check.

## Build order (after design is set)
surface hook + FlatListBody (shared) → lean ListView → GroupListView (move grouping in) →
BoardView (extract lanes) → DataPage opt-in API → update the notes page to opt into
GroupListView → verify (typecheck + base tests + e2e + a bundle/import-graph check that
ListView is grouping-free).
