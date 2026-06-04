# pkg-base structural audit

Scope: `packages/base` (`@angee/base`), the single rendered binding. Judged against
`AGENTS.md`, `docs/guidelines.md`, `docs/frontend/guidelines.md`, `docs/stack.md`.
STRUCTURE only (TS/hooks correctness is a separate pass).

- id: pkgbase-001
  loc: packages/base/src/views/list-view-utils.ts:46
  category: wrong-level-placement
  severity: high
  rule: docs/frontend/guidelines.md "Python ships schema and operations. TypeScript ships UX. React does not own business logic, permissions, models"; AGENTS.md "never push product specifics down into the framework"
  finding: Framework styled layer hardcodes one app's lifecycle vocabulary — STATUS_ORDER ["DRAFT","IN_REVIEW","ACTIVE","ARCHIVED"] and the field name "status" (list-view-utils.ts:17,46; group-list-view.tsx:355) — duplicating the example's note-status.ts; every consumer addon inherits a foreign enum.
  fix: Delete STATUS_ORDER and the "status"/"title" literals from base; derive facet/group fields and order from the server-emitted column/enum schema the SDK already carries (column.tone keys, schema choices), keeping product vocabulary in the addon.
  status: open

- id: pkgbase-002
  loc: packages/base/src/views/data-view-model.ts:36
  category: find-the-owner
  severity: high
  rule: AGENTS.md "Put behavior on the object that owns the data … a function that takes an object and inspects it"; docs/guidelines.md "Put Behavior on the Owning Object"
  finding: DataViewState is a passive interface and data-view-model.ts is a 417-line module of free functions that read/normalise/serialise its shape (createDataViewState, dataViewReducer, dataViewStateToSearch, dataViewStateToResourceListOptions, dataViewSortToResourceOrder, hasFilter, normalise*); collection/instance behavior over the state has no owning class.
  fix: Fold the state behavior onto an owning abstraction (a DataViewState class or the existing DataViewProvider/context value) so the methods live on the object that owns the data instead of decoding it from outside; keep only the pure URL codec free.
  status: open

- id: pkgbase-003
  loc: packages/base/src/views/group-list-view.tsx:81
  category: code-bigger-not-smarter
  severity: high
  rule: docs/guidelines.md "The code is bigger instead of smarter" (DRY); AGENTS.md "Prefer deletion to abstraction … add an abstraction only when it removes real duplication"
  finding: ListView (ListView.tsx) and GroupListView (group-list-view.tsx) are near-identical: same wrapper trio (Body/Bound/Provider), same toolbar/ControlBand/SelectionBar/FlatListBody/bulk-delete scaffold; GroupListView is a strict superset (group + board branches). ListViewBody is GroupListViewBody minus grouping, copied.
  fix: Collapse to one list-view component (the superset) whose grouping/board affordances are gated by props/state; delete the lean ListView copy or make it a thin preset of the same body.
  status: open

- id: pkgbase-004
  loc: packages/base/src/views/list-view-utils.ts:94
  category: find-the-owner
  severity: high
  rule: AGENTS.md "a function that takes an object and inspects it to decide something … Put behavior on the object that owns the data"; docs/stack.md (strawberry-django owns filter operators on the backend; SDK owns resource I/O)
  finding: A module of free functions repeatedly decode the DataViewFilter shape and GraphQL lookup operators from outside (facetFilter, statusFilterValues, textFilterValue, nextTextFilter, nextFacetFilter) with `as Record<string, unknown>` casts of {exact,inList,iContains}; the filter operator vocabulary has no typed owner and is re-decoded per call site.
  fix: Give the filter its owner — a typed Filter value (model owner) with methods to read/toggle a facet and text term, and type the lookup operators (exact/inList/iContains) once instead of casting Record<string,unknown> at each read.
  status: open

- id: pkgbase-005
  loc: packages/base/src/views/list-internals.tsx:865
  category: dry-duplication
  severity: high
  rule: docs/guidelines.md DRY "Reuse existing, well-tested code"; AGENTS.md "Same shape in three places: extract the smallest boring primitive"
  finding: titleCase is defined four times (list-internals.tsx:865, toolbars/DataToolbar.tsx:548, views/DataPage.tsx:586, chrome/menu-tree.ts:165) — three byte-identical, menu-tree's variant already drifted (splits on "." and omits the camelCase space), proving the copies diverge.
  fix: Move one titleCase into lib/ (its owning level, beside cn) and import it; delete the four copies.
  status: open

- id: pkgbase-006
  loc: packages/base/src/views/data-view-context.tsx:108
  category: dead-code
  severity: medium
  rule: AGENTS.md "Prefer deletion to abstraction … Before structural refactors, remove dead code first"; "options or params nothing uses"
  finding: DataViewContextValue.resourceListOptions and its useCallback wiring (data-view-context.tsx:43-48,108-115) and the underlying dataViewStateToResourceListOptions/dataViewSortToResourceOrder export are never called by any consumer (only their own definitions + tests); useDataViewSurface builds list options inline instead.
  fix: Remove resourceListOptions from the context value and drop the unused model export (or route useDataViewSurface through it if it is meant to be the single source).
  status: open

- id: pkgbase-007
  loc: packages/base/src/chrome/menu-tree.ts:89
  category: find-the-owner
  severity: medium
  rule: AGENTS.md "Put behavior on the object that owns the data"; docs/guidelines.md "collection behavior lives on the collection abstraction"
  finding: ChromeMenuItem/MenuTree are passive shapes with a sibling module of free decoders (menuItemTarget, menuItemLabel, menuItemIcon, menuItemMatchesPath, menuParentId) and collection traversals (railMenuItems, appSectionItems, activeAppRoot) reaching into them; topMenuItems is a dead synonym that just `return railMenuItems(itemsOrTree)`.
  fix: Put item accessors and tree queries on owning types (item helpers as MenuItem methods/getters, traversals on a MenuTree class) and delete the topMenuItems synonym in favor of railMenuItems.
  status: open

- id: pkgbase-008
  loc: packages/base/src/views/list-internals.tsx:624
  category: dry-duplication
  severity: medium
  rule: AGENTS.md "Keep one source of truth per fact … a function that switches on a value's type wants polymorphism"; docs/frontend/guidelines.md "Use shared … primitives before adding new local state"
  finding: cellContent switches on column shape (render / tone-badge / array-chips / date) to format cells, re-implementing the statusBadge/tagInput/date widget rendering that the widgets/ registry already owns; list cells take a second formatting path that bypasses the WidgetDefinition.cell components.
  fix: Resolve list cells through the widget registry (column.widget → WidgetDefinition.cell) so badge/date/array formatting has one owner; keep cellContent only as the fallback for columns without a widget. (Verify this is not a deliberate lean-column path before collapsing.)
  status: open

- id: pkgbase-009
  loc: packages/base/src/views/board-view.tsx:38
  category: inconsistent-naming
  severity: medium
  rule: docs/guidelines.md "One concept, one name, everywhere … Encode the role in the name, consistently"; "Follow the host framework's conventions exactly"
  finding: views/ mixes the file-naming convention for the same artifact kind: component-exporting files board-view.tsx (BoardView), group-list-view.tsx (GroupListView), grouped-list.tsx (GroupedListBody), list-internals.tsx (FlatListBody/RecordRow/SelectionBar) are kebab-case while the sibling *View components ListView.tsx, FormView.tsx, DataPage.tsx, AggregatePanel.tsx are PascalCase — the package convention elsewhere (chrome/, page/, shell/) is PascalCase component file = component, kebab = utility module.
  fix: Rename component-exporting files to PascalCase to match the rest of base (BoardView.tsx, GroupListView.tsx); keep kebab only for true utility modules (data-view-model.ts, list-view-utils.ts).
  status: open

- id: pkgbase-010
  loc: packages/base/src/fragments/DataLens.tsx:33
  category: lifted-unearned-code
  severity: low
  rule: docs/guidelines.md "The code is bigger instead of smarter" / red flags; AGENTS.md "Prefer deletion to abstraction. Add an abstraction only when it removes real duplication"
  finding: DataLens is a 282-line speculative surface (five visual modes graph/chart/metrics/map/tree, a generic project<TRow,TSchema> callback, QueryStateValues<TSchema>) with no consumer in the app — only a Storybook story; the generality (two type params, Record<string,unknown> schema) is unearned by any caller.
  fix: Defer to the owning library when a real consumer appears (stack.md routes graph→@xyflow, chart/metrics→their owners); until then trim DataLens to the modes actually rendered, dropping the generic project/schema parameters.
  status: open

- id: pkgbase-011
  loc: packages/base/src/views/data-view-model.ts:23
  category: record-unknown-missing-type
  severity: low
  rule: docs/stack.md "TypeScript … Branded boundary types"; docs/guidelines.md "Let Code Carry Code Contracts"
  finding: Genuine modeled shapes are typed as Record<string, unknown> rather than a named type — DataViewFilter (data-view-model.ts:23) and the filter-lookup reads in list-view-utils.ts (103,110,112,121) — so the filter-operator contract is reasserted by casting at each use. (The ~24 other scanner hits in ui/*.tsx are the legitimate Base UI render/asChild prop-spread pattern, not findings.)
  fix: Name the filter-operator union (e.g. exact/iContains/inList) as a type owned beside DataViewFilter and reuse it, removing the per-call `as Record<string, unknown>` casts.
  status: open
