# React Consistency & DRY — Exhaustive TODO

Trackable task list for the cleanup. Rationale + evidence:
`react-consistency-cleanup.md` (same dir). Branch: `workspace/react-consistency`.
Decisions locked 2026-06-14: **i18n=commit · color=two-axis (tone × variant) ·
defineBaseAddon=yes · speculative fragments=keep (deferred)**.

Convention: `[ ]` todo · `[~]` in progress · `[x]` done. Each item names the
owner that absorbs the fix. Verify per `docs/frontend/guidelines.md` Checks
(`pnpm run typecheck && pnpm run test && pnpm run build`) before marking done.

---

## Phase 0 — Quick correctness wins (small, high-value)

- [ ] **T18** Add Tailwind `@source` entries for composed addons in host CSS
      (`examples/notes-angee/web/src/index.css`): `integrate/web/src`,
      `storage/web/src`, `knowledge/web/src`. Verify their arbitrary classes
      (`max-w-[1100px]`, `max-w-[820px]`) now generate.
- [ ] **T12** One `runActionResult(outcome)` for `{ ok, message }` actions
      (`if (!ok) throw new Error(message); return message`), exported from
      `@angee/sdk`. Wire integrate `IntegrationsPage`/`SourcesPage`/`WebhooksPage`/
      `VCSIntegrationsPage` (+ confirm agents `InferencePage` matches). Kills the
      green-toast-on-failure bug.
- [ ] **T11** Route aggregate reads through the model refetch/invalidation
      registry: add `useRegisterModelRefetch` + return `refetch` from
      `useResourceAggregate`/`useResourceGroupBy` (`packages/sdk/src/aggregates.ts`),
      ideally via a shared `useModelDocumentQuery` wrapper. Confirm `AggregatePanel`
      /`GroupedList` live-refresh.
- [ ] **T10c** Delete dead `createSchemaClients` (`graphql-provider.tsx`); point its
      lone test at `GraphQLClientProvider`. Drop dead `by` alias on
      `GroupByDimension` (`aggregates.ts`). Remove dead import in
      `storage/web/src/data/file-rows.ts`.

## Phase 1 — Design-system spine (biggest UX lever)

### T1 — Two orthogonal color axes (`tone` × `variant`)
- [ ] Define the canonical palette enum (`tone`/`color`: brand, neutral, info,
      success, warning, danger, accent?) and the fill enum (`variant`: solid,
      soft, surface, outline, ghost) as exported constants in `@angee/base`.
- [ ] Make `lib/tones.ts` the **(palette × fill) → class** matrix owner; export
      `tones`, `ToneName`, fill variants, and the option constants from
      `lib/index.ts` (barrel currently omits `tones`/`slot`).
- [ ] Rename/restructure recipes to the two-axis model: `badge` (its `variant`
      becomes `tone`+`variant`), `chip`, `alert`, `alert-dialog`, `code`,
      `status-icon`/`status-dot`, `slider`, `kbd`, `section-eyebrow`,
      `selection-bar`, `avatar` (drop numeric `color`), `metric-grid`, chrome menu
      tones, `BrandButton`, `DataLens`, `MarketingHero`, `PageAside`.
- [ ] Retire `error` → `danger` everywhere (Toast `ToastIntent`/`TOAST_TONES`).
- [ ] Update Storybook `argTypes` to consume the shared option constants; remove
      inline `status→tone` maps (`Page.stories`, `SplitView.stories`).
- [ ] Codemod/replace consumer usages of the old prop names across base + addons.

### T2 — `lib/tones.ts` bypassed + overloaded
- [ ] Route `chip`/`alert`/`alert-dialog`/`AppChooser.toneClass` through `tones`
      (no hand-typed soft-tone triples). (Folds into the T1 recipe pass.)
- [ ] Move `DEFAULT_STATE_TONE_VALUES` / `stateToneFromValue` business semantics
      OUT of base → model/field metadata or explicit caller mapping.

### T3 — Intent→glyph duplication
- [ ] One exported `INTENT_GLYPHS` next to `tones`; consume in Toast/alert/
      status-icon. Reconcile `info` glyph (`info` vs `help`).

### T5 — Icon registry: kill raw lucide imports
- [ ] Register missing names in `chrome/icon-registry.ts`: `filter`,
      `sliders-horizontal`, `grid-2x2`, plus confirm `chevron-left/right`, `check`,
      `minus`, `search`, `x`, sort/layout glyphs.
- [ ] Give `Glyph` an optional `fallbackName` (default `help`); let `Glyph` accept
      `string | ReactNode` (folds T8 icon-adapter).
- [ ] Replace raw lucide with `<Glyph>` in: `toolbars/DataToolbar`; `ui/`
      (`dialog`, `input`, `select`, `checkbox`, `pager`); `widgets/`
      (`markdown`, `tagInput`); `views/` (`ListInternals`, `RowsListView`,
      `GroupedList`, `DataPage` chevrons); `chrome/AppRail.RailGlyph`.

## Phase 2 — State surfaces (T4)

- [ ] Align prop vocab across `EmptyState`/`InlineEmpty`/`LoadingPanel`/
      `ErrorBanner` (one of `{title,description,icon,actions}`; deprecate
      `label`/`message` aliases or unify).
- [ ] Add a `CollectionStatus` / `DataViewFrame` (table-row + block variants);
      thread `fetching`/`error`/`emptyMessage` through every collection renderer
      (`ListView`, `RowsListView`, `GroupedList`, `BoardView`, `GalleryView`,
      `TreeView`, `TimelineView`). Gallery/Timeline/Tree must render an empty state.
- [ ] Extract the `Spinner + "Loading…"` footer (copied 5×) into one
      `ListLoadingFooter`/`<LoadingRow>` in `ListInternals`.
- [ ] Delete `Chatter`'s local `EmptyState`; use the shared fragment (match
      `RevisionsTab`).
- [ ] Add a centered/full-height variant to `EmptyState`; collapse the
      `grid place-content-center` wrapper (×7 storage/knowledge).
- [ ] Preview renderers: have `useFileText`/`<FileText>` own loading/error so each
      renderer describes only its happy path.

## Phase 3 — Find-the-owner (T8) + SDK/no-owner lifts (T10)

### T8 — move shape-sniffing onto its owner
- [ ] `string|ReactNode` icon adapter → on `Glyph` (done with T5).
- [ ] `recordSubtitleParts` (field-name sniffing + `atob` id decode),
      `AggregatePanel` `${field}Id` guess, `RevisionsTab.revisionSnapshot` →
      SDK/model metadata exposes subtitle parts / aggregate keys / changed fields.
- [ ] `FormView` field-behavior decoders (`widgetId`, `isRelationIdField`,
      `hasOptionValue`, `emptyValue`) → widget/field resolver owns selection path /
      empty value / submit normalization / layout role.
- [ ] option `value→label` resolver (×7 widgets) → one helper on
      `WidgetField.options`.
- [ ] `matchesClientLookup`/`isLookupOperator` → export operator guards from
      `data-view-model`; `Filter` owns text-filter target/predicate.
- [ ] `AppChooser.itemMatchesPath` / `TopMenu` active logic → add
      `isActive(pathname)`/`hasActiveDescendant(pathname)` to `ChromeMenuNode`/
      `MenuTree`.
- [ ] SDK `model-metadata` display heuristics (`enumValueLabel` casing,
      `recordRepresentationFor` order) → keep SDK structural; humanization moves to
      the rendered binding.

### T10 — lift no-owner plumbing to SDK/base lib
- [ ] `unknown→message` error helper (×5) → `@angee/sdk`.
- [ ] `run()` busy-wrapper hook (×3) → `useBusyAction`/`useAsyncAction`.
- [ ] relay `global-id` codec (×2) → `@angee/sdk` (`toRelayGlobalId`,
      `relationRelayGlobalId`).
- [ ] `ActionResultData`/`IdVariables` per addon → export from `@angee/sdk` (pairs
      with T12 `runActionResult`).
- [ ] `titleLabel`/`stateLabel` → reuse base `titleCase`.
- [ ] `pageSize={50}` (×14) → drop, or import `DEFAULT_PAGE_SIZE`.
- [ ] ⌘K handler + `isTextEntryTarget` (×2) → one `useCommandShortcut` (folds T9
      palette work).
- [ ] confirm/prompt promise-queue → one `useQueuedDialog<TOptions,TResult>` in
      `ModalsHost`.
- [ ] SDK mutation hooks → one `useDocumentMutation`; collapse
      `useStableMeasures` into a generic `useStableValue`.

## Phase 4 — Date formatting (T7)

- [ ] Export the date-fns-backed `RelativeTime`/datetime widget for addon use.
- [ ] Delete local `formatDate`/`toLocaleDateString`/`Intl` (views-record
      subtitle, `storage/file-display`, `knowledge/PageEditor`).
- [ ] One `parseRowDate` (date-fns) shared by `ListInternals` + `TimelineView`.

## Phase 5 — De-fork components (T9)

- [ ] `dropdown-menu`/`context-menu` → shared `menuVariants` + `createMenuParts`
      factory.
- [ ] Floating overlay surface → one `OVERLAY_SURFACE`/`POPUP_BASE`; consume in
      popover/nav-menu/dropdown/context/select/tooltip.
- [ ] One data-view shell over injected surface hooks (collapse `ListView`/
      `RowsListView`); fold `GroupedList` table/fetch/status/footer into shared
      surface; one activation/card primitive (list/board/gallery) owning
      `href`/`onActivate`/keyboard/selection/DnD (BoardView reads
      `selectedIds`/`interactive`).
- [ ] One field stack: `field.tsx` is owner; `FieldRow` composes it; shared
      `RequiredMark`/`OptionalHint`; extend `widget-control` surface to
      textarea/field/number-field/select.
- [ ] One tag primitive (`tones`-driven) → `Badge`/`Tag`/`Chip` aliases.
- [ ] One header (collapse `CollectionHeader`/`SurfaceHeader`/`RecordHeader` into
      `PageHeader` + tone token + slots).
- [ ] One `createShellPortalSlot` → `ControlBand` + `Statusline`; align provider
      host types.
- [ ] One `PageChrome`/`TwoPaneFrame` → `CanvasPage`/`RecordView`/`SplitView`/
      `HeroPage`.
- [ ] `DaemonResourceTable` (operator's 6 raw tables) or move to `RowsListView`.
- [ ] `ScopedTreeExplorer` primitive → storage + knowledge browsers.
- [ ] Shared date popover helper (date/datetime); `ChipList`/`TokenList` primitive
      (tagInput/many2many).
- [ ] One command palette: make `Spotlight`/`ui/command` the owner; delete/retire
      `GlobalSearch` (wire the real palette into `TopBar`).
- [ ] `createVariantContext` → tabs/accordion/collapsible; `FormSectionKicker`
      composes `SectionEyebrow`; shared `ControlRow` (switch/radio/field).
- [ ] `InlineNameField` (storage/knowledge ×3); `RefreshingBadge`;
      `SectionNav`/`SectionTabs` one routed section-nav owner; one metric
      `MetricTile`+`MetricCollection`.

## Phase 6 — Architecture decisions (now locked)

- [ ] **T6 (commit)** Expand the `base` i18n bundle; route `ModalsHost`, `auth`,
      `communication`, chrome copy, and all `ui` default labels/aria through
      `useBaseT()`. Give every addon (iam, operator, storage, knowledge, integrate,
      agents) its own namespace + bundle; route all copy through `t`.
- [ ] **T13** Previews via build-time composition: a `previews`/`widgets` map on
      `AddonManifest`, folded fail-fast by `composeAddons`, resolved through
      `AppRuntimeProvider`. Remove the module-global `Map`, the import side-effect,
      and `clearPreviewProvidersForTest`.
- [ ] **T15 (yes)** Export `defineBaseAddon()` from `@angee/base`; migrate iam,
      operator, storage, knowledge, integrate, agents, notes `BaseAddon` literals.
- [ ] **T16** De-leak product specifics from base chrome: move `TopMenu`
      tab→filter to the data-view/route owner; drop note-specific `DEFAULT_TABS`
      and the `DataViewFilter` import; keep `TopMenu` presentation-only.
- [ ] **T17** Forms onto the DSL/registry: replace inline `<form><label>`
      (iam/operator) with `Field`/`FormView` or a shared `LabeledInput`; register
      drive/vault relation-create via `defineAddon` `forms:`; `Notebook.Tab` onto
      `PAGE_ELEMENT_SLOT`; make `FormView` Star/Share host-provided
      (slot/action) and replace `text-amber-500` with a token.
- [ ] **T2b** `UserMenu` onto the menu primitive (stop faking `role="menu"` on a
      popover).

## Phase 7 — Polish (Tier 4)

- [ ] `pagerVariants`/`treeVariants`; replace `Tree.iconColor:string` with
      token-backed `iconTone` (`ToneName`).
- [ ] Standardize default `size` (switch/toggle-group `sm`→`md` or document);
      unify icon-size spelling (`iconSm/Md/Lg` vs `icon/icon-sm`); reconcile
      `badge.shape` vs `chip.shape` defaults.
- [ ] Class-merge: replace `.filter(Boolean).join(" ")` with `cn()` in list views.
- [ ] Pick one primitive namespace-export convention (Select/Tooltip split).
- [ ] Storybook: kill dead `args`/`argTypes` ignored by `render: () =>` (~30
      files); one `runtime-fixtures` owner (provider stack + `jsonResponse` + CSRF,
      reusing `@angee/base/testing`); drop redundant nested `ToastProvider`; fix
      group taxonomy (`Feedback` 1-member; `Page` vs `Layouts`); replace
      hex/`white` hero literals with inverse tokens.
- [ ] `useOperatorSnapshot` `want*` flags → one `SECTION_KEYS` table; principled
      List/Form declaration placement in agents; collapse two translators
      (`useT`/`translateWithFallback`).

## Exhaustive fixes (deferred — track, don't do yet)

- [ ] **T19** ~12 storybook-only fragments (`FocusPanel`, `ListPanel`,
      `HeroPanel`, `MarketingHero`, `DataLens`, `MetadataPanel`,
      `CollectionHeader`/`SurfaceHeader`/`RecordHeader`, `EditorHeaderBar`,
      `FrameToolbar`, `SectionTabs`): **kept for now** per decision. Revisit per
      fragment on first real consumer — delete vs promote. Note added so they
      aren't mistaken for live API.
- [ ] Full sweep for any remaining hard-coded copy / raw lucide / `cn` violations
      after Phases 1–7 land (drift check: `grep` for `from "lucide-react"` outside
      icon-registry, `toLocaleDateString`, `error` tone, `.join(" ")` class merges).
- [ ] Update `docs/frontend/guidelines.md` + `docs/stack.md` to encode the new
      rules (two-axis color, i18n-commit, `defineBaseAddon`, icon-registry-only,
      one state-surface API) once the owners land — so docs reference the code.
