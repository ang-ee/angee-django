# React Consistency & DRY — Exhaustive TODO

Trackable task list for the cleanup. Rationale + evidence:
`react-consistency-cleanup.md` (same dir). Branch: `workspace/react-consistency`.
Decisions locked 2026-06-14: **i18n=commit · color=two-axis (tone × variant) ·
defineBaseAddon=yes · speculative fragments=keep (deferred)**.

Convention: `[ ]` todo · `[~]` in progress · `[x]` done. Each item names the
owner that absorbs the fix. Verify per `docs/frontend/guidelines.md` Checks
(`pnpm run typecheck && pnpm run test && pnpm run build`) before marking done.

---

## Phase 0 — Quick correctness wins (small, high-value) ✅ done (commit)

Also: `agents/web` `test` → `--passWithNoTests` (the one zero-test package was
failing the `pnpm run test` gate). Review-driven: `agents/InferencePage` aligned
onto `runActionResult` (was hand-rolling it); `WebhooksPage.rotate` ({ok,secret})
now guards `ok` too. `runActionResult` exports only the function (the
`ActionResult`/`ByIdVariables` type consolidation is deferred to Phase 3 / T10).

- [x] **T18** Add Tailwind `@source` entries for composed addons in host CSS
      (`examples/notes-angee/web/src/index.css`): `integrate/web/src`,
      `storage/web/src`, `knowledge/web/src`. Verify their arbitrary classes
      (`max-w-[1100px]`, `max-w-[820px]`) now generate.
- [x] **T12** One `runActionResult(outcome)` for `{ ok, message }` actions
      (`if (!ok) throw new Error(message); return message`), exported from
      `@angee/sdk`. Wire integrate `IntegrationsPage`/`SourcesPage`/`WebhooksPage`/
      `VCSIntegrationsPage` (+ confirm agents `InferencePage` matches). Kills the
      green-toast-on-failure bug.
- [x] **T11** Route aggregate reads through the model refetch/invalidation
      registry: add `useRegisterModelRefetch` + return `refetch` from
      `useResourceAggregate`/`useResourceGroupBy` (`packages/sdk/src/aggregates.ts`),
      ideally via a shared `useModelDocumentQuery` wrapper. Confirm `AggregatePanel`
      /`GroupedList` live-refresh.
- [x] **T10c** Delete dead `createSchemaClients` (`graphql-provider.tsx`); point its
      lone test at `GraphQLClientProvider`. Drop dead `by` alias on
      `GroupByDimension` (`aggregates.ts`). Remove dead import in
      `storage/web/src/data/file-rows.ts`.

## Phase 1 — Design-system spine (biggest UX lever)

### T1 — Two orthogonal color axes (`tone` × `variant`) ✅ done (commit)
Decisions executed: **(1) full token matrix** (added `--P` solid / `--on-P` /
`--P-line` / `--P-tint` surface for every palette in both themes) · **(2)
`default` → `neutral`** as the canonical neutral tone. Verified repo-wide:
`typecheck` + `test` + `build` all green; the new `bg-*`/`text-on-*`/`*-line`/
`*-tint` utilities confirmed present in the built CSS; drift greps clean. Manual
visual-parity spot-check across both themes still recommended before release.
- [x] `TONES`/`Tone` + `FILLS`/`Fill` exported as constants from `@angee/base`
      (`lib/tones.ts` → `lib/index.ts` → package index).
- [x] `lib/tones.ts` is the **(tone × fill) → class** matrix owner (`toneFill`
      45 literal entries + `toneClass(tone, fill)`); barrel now also re-exports
      `slot`. Token layer + Tailwind-merge groups (driven from `TONES`) extended
      so `cn()` de-dupes the new utilities.
- [x] Recipes restructured: `badge` (split `variant`→`tone`+`variant`), `chip`
      (+ `muted`/`inherit` local rows; `outline` bool → `variant:"outline"`),
      `alert` (`intent`→`tone`; `surface`→`format`), `alert-dialog`
      (`intent`→`tone`), `code` (`variant`→`tone`; bg `surface`→`box`),
      `status-icon`/`status-dot`, `slider`, `metric-grid`, `DataLens` (dots via
      `StatusDot`), `DirtyPill`, chrome menu tones (`AppChooser.toneClass` routed
      through the matrix; `ChromeMenuTone` `muted`→`neutral`), `avatar` (numeric
      `color` prop dropped). **Out of the palette axis (kept their own
      vocabulary):** `Kbd.tone` (key-surface), `BrandButton.tone` (social),
      `MarketingHero`/`AnnouncementChip` (inverse), `SelectionBar` (surface),
      `SectionEyebrow` (curated text set). `PageAside` — todo over-listed it; it
      has no color axis (nothing to do). `TopMenu`'s fixed `bg-brand-soft` active
      style left inline (a constant brand treatment, not a per-tone bypass).
- [x] Retired `error` → `danger` (Toast: `ToastIntent`→`ToastTone`, `intent`→
      `tone`, `toast.error()`→`toast.danger()`; `TOAST_TONES` collapsed).
- [x] Storybook `argTypes` consume `TONES`/`FILLS`; inline `status→tone` maps in
      `Page.stories`/`SplitView.stories` replaced with `stateToneFromValue`.
- [x] Consumers codemodded across base, storybook, all six addons + the notes
      example. Operator `StateTag.tsx` `TODO(S2)` workaround maps deleted (the
      palette now exposes `neutral`).

### T2 — `lib/tones.ts` bypassed + overloaded
- [x] Route `chip`/`alert`/`alert-dialog`/`AppChooser.toneClass` through `tones`
      (no hand-typed soft-tone triples). (Done with the T1 recipe pass — also
      `DataLens` dots and `code`-block soft now route through the matrix.)
- [ ] Move `DEFAULT_STATE_TONE_VALUES` / `stateToneFromValue` business semantics
      OUT of base → model/field metadata or explicit caller mapping.

### T3 — Intent→glyph duplication ✅ done (commit)
- [x] One exported `INTENT_GLYPHS` + `FeedbackIntent` in `lib/tones.ts`; consumed
      by Toast/alert/status-icon. Reconciled `info` glyph to `"info"` everywhere
      (Alert/StatusIcon were `"help"`); StatusIcon's `muted` keeps `"help"`.

### T5 — Icon registry: kill raw lucide imports (app surfaces ✅ done; rest deferred)
- [x] Register missing generic names in `chrome/icon-registry.ts`: arrow-down,
      arrow-up, arrow-up-down, filter, grid-2x2, layout-grid, sliders-horizontal.
- [x] Give `Glyph` an optional `fallbackName` (used by `AppRail`, default `help`).
      (The `string | ReactNode` union is the separate T8 icon-adapter — Phase 3.)
- [x] Replace raw lucide with `<Glyph>` in the always-under-runtime surfaces:
      `chrome/AppRail`, `toolbars/DataToolbar`, `views/ListInternals`,
      `views/GroupedList`, `views/RowsListView`, `views/DataPage`,
      `widgets/tagInput`.
- [ ] DEFERRED — low-level `ui/` primitives (`dialog`, `input`, `select`,
      `checkbox`, `pager`): `Glyph`→`useIcon`→`useAppRuntime` returns the EMPTY
      runtime (renders null) when no provider is mounted, so routing a primitive's
      intrinsic check/chevron/X through `Glyph` makes it vanish in any
      provider-less embedding. Needs a design call (do `@angee/base` primitives
      depend on the runtime, or does `Glyph` take a fallback icon component?).
- [ ] DEFERRED — `widgets/markdown` editor toolbar: passes lucide *components*
      into a command/toolbar config (not inline JSX), so it needs its toolbar
      glyphs registered (bold, italic, code-xml, link, list-ordered, quote, eye)
      and the config reshaped to glyph names — a larger, careful change.

## Phase 2 — State surfaces (T4)

- [ ] Align prop vocab across `EmptyState`/`InlineEmpty`/`LoadingPanel`/
      `ErrorBanner` (one of `{title,description,icon,actions}`; deprecate
      `label`/`message` aliases or unify).
- [x] Gallery/Timeline/Tree now render an empty state (was blank): `emptyMessage`
      prop (default "No records.") + centered empty render on each; `RowsListView`
      threads `emptyMessage` into its grid-mode `GalleryView`.
- [ ] (larger) A `CollectionStatus`/`DataViewFrame` abstraction threading
      `fetching`/`error`/`emptyMessage` uniformly through every renderer — still
      open (the empty-state gap above is the high-value piece; the full frame is a
      bigger de-fork).
- [x] Extract the `Spinner + "Loading…"` blocks into `ListLoadingFooter` (footer
      div, was ×3: ListView/RowsListView/GroupedList) + `ListLoadingInline` (span,
      was ×2: GroupedList) in `ListInternals`; dropped 3 now-unused Spinner imports.
- [ ] Delete `Chatter`'s local `EmptyState`; use the shared fragment (match
      `RevisionsTab`).
- [ ] Add a centered/full-height variant to `EmptyState`; collapse the
      `grid place-content-center` wrapper (×7 storage/knowledge).
- [ ] Preview renderers: have `useFileText`/`<FileText>` own loading/error so each
      renderer describes only its happy path.

## Phase 3 — Find-the-owner (T8) + SDK/no-owner lifts (T10)

### T8 — move shape-sniffing onto its owner
- [x] `string|ReactNode` icon adapter → `renderGlyph(icon)` in `chrome/Glyph.tsx`;
      removed the 8 byte-identical local `render<X>Icon` helpers (EmptyState,
      InlineEmpty, MetricStrip, MetricGrid, MiniCard, SurfaceHeader, RecordHeader,
      CollectionHeader) and dropped their now-unused `Glyph` imports.
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
- [x] `unknown→message` error helper → `@angee/sdk` `errorMessage`; routed 8
      sites (3 local defs removed + 5 inline ternaries) across iam/operator/
      integrate/storage. (validation-errors & useBulkDelete left — different intent.)
- [x] `run()` busy-wrapper hook (×3) → SDK `useBusyRun(onChanged)` ({busy, run<T>});
      storage useFile/useFolderActions + knowledge usePageActions consume it
      (+ unit test for the busy/onChanged/throw contract).
- [x] relay `global-id` codec (×2) → `@angee/sdk` (`toRelayGlobalId`,
      `relationRelayGlobalId`); storage/knowledge re-export + keep `*_TYPE` consts.
- [x] `ActionResultData`/`IdVariables` per addon → SDK `ActionOutcome`/
      `ByIdVariables`; integrate + agents `documents.ts` alias them (named
      `ActionOutcome` to avoid colliding with base's `ActionResult` = Action.run).
- [~] `titleLabel` → base `titleCase` — LEAVE SEPARATE (different intent, per DRY):
      `titleLabel` splits on `/` and `:` and does NOT camelCase-split; `titleCase`
      camelCase-splits and ignores `/`,`:`. Routing iam onto `titleCase` would change
      rendered labels AND alter a shared base primitive. (`stateLabel` already gone —
      T1 reworked operator `StateTag`.)
- [x] `pageSize={50}` → dropped from 18 `<List>`/`<ListView>` sites (resolves to
      `DEFAULT_PAGE_SIZE`=50 via the data-view default chain). The 4
      `<RowsListView pageSize={50}>` sites left explicit (different component) —
      harmless (equal to default); a later sweep could drop them too.
- [ ] ⌘K handler + `isTextEntryTarget` (×2) → one `useCommandShortcut` (folds T9
      palette work).
- [ ] confirm/prompt promise-queue → one `useQueuedDialog<TOptions,TResult>` in
      `ModalsHost`.
- [ ] SDK mutation hooks → one `useDocumentMutation`; collapse
      `useStableMeasures` into a generic `useStableValue`.

## Phase 4 — Date formatting (T7)

- [x] One `parseRowDate` (date-fns) in `ListInternals`, consumed by `TimelineView`
      (both had their own `parseDate`; list strings now ISO-parse like the
      timeline — the intended consistency fix).
- [x] Addon `formatDate`/`toLocaleDateString` — already gone (no occurrences in
      `addons/*/web/src`; removed by earlier work). Moot.
- [~] Remaining date usage is `FormView` record-subtitle `Intl.DateTimeFormat`
      (`FormView.tsx:1241`) — folded into **T8** (SDK exposes subtitle parts;
      formatting moves to the date-fns owner there). Base `toLocaleString()` calls
      are NUMBER formatting (counts), not dates — out of T7 scope.
- [~] "Export `RelativeTime` for addon use" — moot for now (no addon `formatDate`
      consumer remains); revisit if a custom addon list needs it.

## Phase 5 — De-fork components (T9)

- [~] `dropdown-menu`/`context-menu` → shared `menuVariants` + `createMenuParts`
      factory — DEFERRED (not a clean mechanical dedup): the two diverge in real
      API (dropdown has `<Payload>` generics + a `Viewport` part; context has a
      styled `trigger` slot). A factory must parameterize those, so it's a
      deliberate design, best done as its own focused effort. (Their shared
      *surface* class is already deduped via POPUP_BASE — see next item.)
- [x] Floating overlay surface → consume `POPUP_BASE` (popover's exported content
      surface) in nav-menu (`popup` slot), select, dropdown, context. Tooltip left
      separate (uses `bg-tooltip`, a different surface).
- [ ] One data-view shell over injected surface hooks (collapse `ListView`/
      `RowsListView`); fold `GroupedList` table/fetch/status/footer into shared
      surface; one activation/card primitive (list/board/gallery) owning
      `href`/`onActivate`/keyboard/selection/DnD (BoardView reads
      `selectedIds`/`interactive`).
- [ ] One field stack: `field.tsx` is owner; `FieldRow` composes it; shared
      `RequiredMark`/`OptionalHint`; extend `widget-control` surface to
      textarea/field/number-field/select.
- [~] One tag primitive (`tones`-driven) — RESOLVED by T1 / LEAVE SEPARATE: the
      audit's core complaint (chip hard-coding soft tones) is fixed — `Badge` and
      `Chip` both route color through `toneClass(tone, variant)` now. Merging them
      into one primitive is NOT clean: different size scales (`density` all
      `h-tag-h` vs `size` `h-tag-h`/`h-5`/`h-6`), bases (`min-w-0` vs
      `truncate shrink-0`), defaults (`rounded` vs `pill`), and Chip-local tones
      (`muted`/`inherit`) — different intents (status/count pill vs removable
      token). Per DRY "different intent: leave separate." (`Tag` is already a thin
      `Badge` alias.)
- [ ] One header (collapse `CollectionHeader`/`SurfaceHeader`/`RecordHeader` into
      `PageHeader` + tone token + slots).
- [x] One `createShellBand` factory → `ControlBand` + `Statusline` are thin
      aliases; aligned `StatuslineProvider` host type to include `undefined`.
      (shell/shell-band.tsx; all exports + StatusSegment/Spacer preserved.)
- [ ] One `PageChrome`/`TwoPaneFrame` → `CanvasPage`/`RecordView`/`SplitView`/
      `HeroPage`.
- [x] `DaemonResourceTable` (operator `views/parts`) — one generic table
      (columns/rows/actions/busy/emptyMessage) replaces the hand-built `<Table>` in
      all 6 sections (Services/Sources/Workspaces/Operations/Secrets/GitOps);
      behavior-preserved (colSpans, actions, confirm flows, OperatorSection chrome).
      Follow-ups (review, non-blocking): the `runDaemonAction({name})` adapter is
      still spelled 3× (Services/Sources/Workspaces) — a thin `useDaemonRowActions`
      helper could own it; cell-kind classes (muted/numeric) are inline per column;
      SecretsSection's Protected/Delete is a column (action API can't render a
      withheld cell).
- [ ] `ScopedTreeExplorer` primitive → storage + knowledge browsers.
- [ ] Shared date popover helper (date/datetime); `ChipList`/`TokenList` primitive
      (tagInput/many2many).
- [ ] One command palette: make `Spotlight`/`ui/command` the owner; delete/retire
      `GlobalSearch` (wire the real palette into `TopBar`).
- [x] `createVariantContext` (`lib/variant-context.tsx`) → tabs/accordion/
      collapsible consume it (replaced 3 local `createContext + useXVariant`).
- [ ] `FormSectionKicker` composes `SectionEyebrow`; shared `ControlRow`
      (switch/radio/field). (Still open — separate from the variant-context dedup.)
- [ ] `InlineNameField` (storage/knowledge ×3); `RefreshingBadge`;
      `SectionNav`/`SectionTabs` one routed section-nav owner; one metric
      `MetricTile`+`MetricCollection`.

## Phase 6 — Architecture decisions (now locked)

- [~] **T6 (commit)** — IN PROGRESS, incremental:
      - [x] base primitives: `ModalsHost` (confirm/cancel/done), `dialog` (close),
        `alert` (dismiss), `pager` (prev/next/rows-per-page/apply), `selection-bar`
        (clear/selected), `number-field` (inc/dec), `ListLoadingFooter/Inline`
        (loading) → `useBaseT()` + `enBaseMessages`.
      - [x] base `auth` (UsernamePasswordForm/LoginPage) + `communication`
        (Chatter/RevisionsTab) → `useBaseT()` + `enBaseMessages` (auth.*/revisions.*
        /chatter.*). LoginHero marketing copy left (one-off; key later if wanted).
      - [x] base chrome (AppChooser/Spotlight/GlobalSearch/UserMenu/Systray/TopBar/
        AppRail/Breadcrumb) → `useBaseT()` + `chrome.*` keys (also stabilized
        `useBaseT` with `useCallback`). `TopMenu` `DEFAULT_TABS` left for T16.
      - [ ] base minor remainder: `toolbars/DataToolbar` copy, `ListInternals`
        `SelectionBar` wrapper (Clear/Delete) + row aria-labels, pager `subject`.
      - [x] per-addon namespace + bundle, all copy routed: iam, operator, storage,
        knowledge, integrate, agents (~280 keys, produced by 6 parallel agents).
      - [x] unified translator owner: added `useNamespaceT(ns, fallback)` to
        `@angee/sdk`; `useBaseT` + every addon's `use<Addon>T()` build on it (so a
        component renders English provider-less — fixed iam's failing tests).
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
- [x] Class-merge: `.filter(Boolean).join(" ")` → `cn()` in `DataPage`/`GraphView`
      (BrandButton's is an aria-id join, left alone).
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

## Infra note (out of band — flagged 2026-06-14)

- [ ] **Dev workspace template: operator port is hardcoded, not workspace-
      allocated.** `.angee/process-compose.yaml` runs `angee operator … --port
      9000` literally, while `.angee/angee.yaml` documents the operator port as
      workspace-allocated (`operator.port_pool.operator`, "never a hardcoded
      9000"). Django/UI/storybook/process-compose ports ARE allocated per
      workspace (react-consistency 8100/5173/6006/8080 vs agents-addon
      8103/5177/.../10002), so two workspaces' operator daemons both try to bind
      9000 and collide. Fix the `templates/workspaces/dev` (and/or stacks/dev)
      process-compose template to use `${ports.operator}` instead of literal 9000.
      (Backend/template, not React — tracked here so it isn't lost.)
