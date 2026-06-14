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

### T5 — Icon registry: kill raw lucide imports ✅ done (commit)
- [x] Register missing generic names in `chrome/icon-registry.ts`: arrow-down,
      arrow-up, arrow-up-down, filter, grid-2x2, layout-grid, sliders-horizontal.
- [x] Give `Glyph` an optional `fallbackName` (used by `AppRail`, default `help`).
      (The `string | ReactNode` union is the separate T8 icon-adapter — Phase 3.)
- [x] Replace raw lucide with `<Glyph>` in the always-under-runtime surfaces:
      `chrome/AppRail`, `toolbars/DataToolbar`, `views/ListInternals`,
      `views/GroupedList`, `views/RowsListView`, `views/DataPage`,
      `widgets/tagInput`.
- [x] DONE (Glyph fallback, then route): `useIcon` falls back to the static
      `baseIcons` map when the runtime registry lacks the name (so registry glyphs
      resolve provider-less), and the 5 primitives (`dialog`/`input`/`select`/
      `checkbox`/`pager`) route through `<Glyph>` with raw lucide dropped. Review
      caught the lost stroke weight: gave `Glyph` a `strokeWidth` passthrough (one
      owner), restored checkbox=3 / dialog=2.25, and replaced status-icon's
      `[&_*]:stroke-[2.25]` CSS hack with it.
- [x] DONE — `widgets/markdown` editor toolbar: registered the toolbar glyphs
      (bold, italic, code-xml, link, list-ordered, quote, eye) and changed
      `ToolbarButton.icon`/`ModeButton` from a lucide component to a glyph name
      routed through `<Glyph>`. **T5 complete** — repo-wide drift check confirms no
      `from "lucide-react"` imports remain outside `chrome/icon-registry.ts`.

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
- [x] option `value→label` resolver → one `optionLabel(options, value)` owner in
      `widgets/types.ts` (beside `WidgetOption`); routed select, combobox, many2one,
      statusBadge, many2many (its local copy deleted). Left separate (different
      intent): ownerCell (object value), RelationField (finds the option object),
      themePicker (local list + "System" fallback), booleanBadge (boolean match).
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
- [x] ⌘K handler + `isTextEntryTarget` (×2) → resolved by the command-palette
      work: `GlobalSearch` (the second copy) is deleted, so the one remaining
      handler lives on `Spotlight`'s `useSpotlightShortcut` (now ref-stable).
- [x] confirm/prompt promise-queue → one generic `useQueuedDialog<TOptions,TResult>`
      in `ModalsHost` (the FIFO request-queue owner: list + `active` head + stable
      `enqueue`/`resolveActive`). Confirm and prompt now each instantiate it;
      ~50 lines of duplicated plumbing + the two module id counters removed.
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
- [~] Data-view shell — the FOUNDATION already shipped pre-session
      (`views/data-view-surface.ts` `useDataViewSurface`/`useRowsDataViewSurface`,
      lean `ListView`, `GroupListView` re-export stub, shared `FlatListBody` in
      `ListInternals`). Re-scoped the rest against the current code:
      • **ListView/RowsListView — clean, LEAVE**: both lean, share `FlatListBody`
        + the presentation surface; the only difference is the data source
        (`useDataViewSurface` server-fetch vs `useRowsDataViewSurface` client-rows)
        — a deliberate, not duplicated, fork.
      • **activation/card primitive — DONE the genuine bit**: extracted
        `dragSourceProps(payload)` into `lib/dnd.ts` (the non-hook companion to
        `useDraggable`; +unit test), routed list rows (`rowDragProps` deleted) and
        gallery cards through it. The broader `ActivatableCard` is LEAVE-SEPARATE:
        list row (`<tr>`, must intercept click+Enter — no native anchor), gallery
        card (`<a>`/button, native or Enter+Space), and board card (click-only, no
        selection/DnD) have different DOM + interaction contracts; one primitive
        would over-parameterize and risk core interactions.
      • **fold `GroupedList` status/footer — LEAVE SEPARATE** (close-read): a folded
        group is a different rendering context than a flat list, so the pieces
        genuinely diverge — status is tristate with group messages + a per-group
        inline loader (vs `FlatListBody`'s empty-only), the footer puts "Total" in
        the select cell with a nullable aggregate (vs `FlatMeasureFooter`'s "Total"
        in the first data column + `selectable`), and each group has its own inline
        `Pager` (flat has none). The only shared bit is the per-column measure-cell
        loop, embedded in divergent footer wrappers — extracting it would
        over-parameterize delicate folded-group/freeze-guard code. Phase 5 data-view
        is complete: the genuine consolidations landed, the rest are different-intent.
- [~] One field stack — DONE the genuine parts: extracted shared `RequiredMark`
      (3 sites: `Label`, `FieldLabel`, `FieldRow`) + `OptionalHint` (2 sites:
      `Label`, `FieldLabel`) into `ui/label.tsx` (the foundational label owner;
      callers pass `className="ml-1"` where there's no parent gap). Behavior-
      preserving. LEFT: `FieldRow` does NOT compose `Field` — their labels have
      different treatments (eyebrow kicker vs `Field.Label`), so composing would
      change rendering (different intent). `widget-control` surface is ALREADY
      shared — textarea/number-field/select all consume `widgetControlSurface`
      (nothing to extend).
- [~] One tag primitive (`tones`-driven) — RESOLVED by T1 / LEAVE SEPARATE: the
      audit's core complaint (chip hard-coding soft tones) is fixed — `Badge` and
      `Chip` both route color through `toneClass(tone, variant)` now. Merging them
      into one primitive is NOT clean: different size scales (`density` all
      `h-tag-h` vs `size` `h-tag-h`/`h-5`/`h-6`), bases (`min-w-0` vs
      `truncate shrink-0`), defaults (`rounded` vs `pill`), and Chip-local tones
      (`muted`/`inherit`) — different intents (status/count pill vs removable
      token). Per DRY "different intent: leave separate." (`Tag` is already a thin
      `Badge` alias.)
- [~] One header (collapse `CollectionHeader`/`SurfaceHeader`/`RecordHeader` into
      `PageHeader`) — LEAVE SEPARATE: all three are **storybook-only** (zero live
      consumers outside `*.stories.*`) and each is already a thin semantic wrapper
      over `page/PageHeader`. Per the T19 decision (keep storybook-only fragments)
      there's no app-level consolidation to do; revisit on first real consumer.
- [x] One `createShellBand` factory → `ControlBand` + `Statusline` are thin
      aliases; aligned `StatuslineProvider` host type to include `undefined`.
      (shell/shell-band.tsx; all exports + StatusSegment/Spacer preserved.)
- [~] One `PageChrome`/`TwoPaneFrame` → `CanvasPage`/`RecordView`/`SplitView`/
      `HeroPage` — LEAVE SEPARATE (scoped): the four layouts serve distinct purposes
      (5-slot record form / 2-slot canvas / draggable split / centered hero) and
      already share the page-chrome via the `findLayoutSlot`/`createLayoutSlot`
      utilities + the optional `Page` wrapper. A forced common frame would overfit;
      none have addon consumers yet (base layout primitives).
- [x] `DaemonResourceTable` (operator `views/parts`) — one generic table
      (columns/rows/actions/busy/emptyMessage) replaces the hand-built `<Table>` in
      all 6 sections (Services/Sources/Workspaces/Operations/Secrets/GitOps);
      behavior-preserved (colSpans, actions, confirm flows, OperatorSection chrome).
      Follow-ups (review, non-blocking): the `runDaemonAction({name})` adapter is
      still spelled 3× (Services/Sources/Workspaces) — a thin `useDaemonRowActions`
      helper could own it; cell-kind classes (muted/numeric) are inline per column;
      SecretsSection's Protected/Delete is a column (action API can't render a
      withheld cell).
- [~] `ScopedTreeExplorer` primitive → storage + knowledge browsers — LEAVE
      SEPARATE (scoped): both already compose the shared `views/TreeView` +
      `layouts/Explorer` primitives; what remains per-addon is genuinely domain-
      specific (row builders `folderTreeRows` vs `pageTreeRows`, file-preview vs
      page-reader content, file vs page actions). No further shared shell to extract.
- [x] Shared date popover helper — `widgets/date-popover.tsx` (`DatePopover` shell +
      `dateFromValue`/`valueLabel`/`DateWidgetValue`); `date` and `datetime` consume
      it, each keeping its own `onSelectDate` (format/close vs preserve-time/stay-open)
      and `footer` (clear vs time-input). Behavior-preserving (review-confirmed).
- [~] `ChipList`/`TokenList` primitive (tagInput/many2many) — LEAVE SEPARATE: the
      chip-row shell matches, but the data contracts diverge (tagInput maps raw
      strings; many2many resolves `optionLabel()` + truncates) and remove handlers
      key differently (index vs value). A unified API would need both shapes —
      extraction cost > savings. Both are widget-internal (no external consumers).
- [x] DONE (wire live + delete): new `chrome/CommandPalette` composes `Spotlight`
      with menu-derived nav commands (`MenuTree.navigableItems()` — the new nav
      source on the owner) + `useNavigate`; wired into `TopBar`; dead `GlobalSearch`
      (+ story) deleted, its orphaned i18n keys removed, the duplicate ⌘K
      handler/`isTextEntryTarget` collapsed onto `Spotlight`. Review-driven:
      repointed the e2e page-object/spec off the deleted `role="search"` to the
      palette button, added `navigableItems` tests, stabilized
      `useSpotlightShortcut` (ref, mounts once), added a CommandPalette story, and
      routed `Spotlight`'s icon adapter through the shared `renderGlyph` owner.
- [x] `createVariantContext` (`lib/variant-context.tsx`) → tabs/accordion/
      collapsible consume it (replaced 3 local `createContext + useXVariant`).
- [x] `FormSectionKicker` was a byte-identical duplicate of `SectionEyebrow` (same
      recipe, only the default `as` differed) — DELETED it, routed its 3 consumers
      (FormView ×2, `FieldRow`) to `SectionEyebrow` (preserving `as`), and removed
      the now-dead `kicker` slot + its 6 variants/type-exports from
      `formLayoutVariants`. Review-confirmed behavior-preserving. `ControlRow` —
      LEAVE: scoping found no such component and no repeated label+control+hint row
      to extract (field-row label logic already lives in `Field`/`FieldRow`).
- [~] `InlineNameField`/`SectionNav`/`SectionTabs` — LEAVE SEPARATE (scoped):
      the inline-title editors are only 2 (knowledge `PageEditor` autosave-on-blur
      vs `NewPageControl` form-submit+cancel) with incompatible save/cancel
      contracts (storage's mention is a comment, not a 3rd impl). `SectionNav`
      (router-agnostic, external `active`) vs `SectionTabs` (a styled `Tabs`
      wrapper owning its own selected state) have different props/state/render
      models and are both storybook-only — unifying needs a `mode` bifurcation
      (anti-DRY). `RefreshingBadge` — does not exist; the nearest (knowledge
      `SaveBadge`, autosave UX) is addon-local, not duplicated. `MetricTile`
      (`MetricStrip`) vs `MetricGrid` — LEAVE SEPARATE: share only the
      Card>icon-chip>label/value/detail skeleton but diverge in every slot (label
      `SectionEyebrow` vs tone-coded `Tag`; value `text-13` vs
      `text-2xl tabular-nums`; detail element, padding, header margin, grid
      breakpoint, `tone`) — a unified `MetricCard` would need ~6 config props for
      2 different-intent consumers (compact summary strip vs dashboard tiles).

## Phase 6 — Architecture decisions (now locked)

- [x] **T6 (commit)** — DONE (base + all 6 addons routed through i18n):
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
      - [x] base minor remainder: `toolbars/DataToolbar` (~30 keys, `dataToolbar.*`),
        `ListInternals` `SelectionBar` (Clear/Delete/`{count} selected`) + row/sort
        aria-labels (`list.*`/`selection.*`), pager `subject` (`pager.records`/
        `pager.pageOf`) → `useBaseT()`. English prop defaults (createLabel,
        pagerSubject, subject, ariaLabel) became `undefined` + in-body coalesce so
        the public API is preserved; sort/page aria strings reproduce exact output.
      - [x] per-addon namespace + bundle, all copy routed: iam, operator, storage,
        knowledge, integrate, agents (~280 keys, produced by 6 parallel agents).
      - [x] unified translator owner: added `useNamespaceT(ns, fallback)` to
        `@angee/sdk`; `useBaseT` + every addon's `use<Addon>T()` build on it (so a
        component renders English provider-less — fixed iam's failing tests).
- [x] **T13** DONE — previews via build-time composition. Deleted the module-global
      `Map`, `registerPreviewProvider`, `previewProviders()`,
      `clearPreviewProvidersForTest`, and the `preview/index.ts` import side-effect.
      `resolvePreviewProvider(providers, mime)` is now pure; built-ins are a static
      `builtinPreviewProviders` const (universal — `PreviewPane` always includes
      them). Addons contribute via a new `previews?` field on `AddonManifest`
      (`PreviewContribution` `{id}`), merged fail-fast by `composeAddons` (mirrors
      widgets/icons), seeded onto `AppRuntime.previews`, read by `usePreviews()`.
      `PreviewPane` resolves `[...usePreviews(), ...builtins]` (runtime first →
      addon overrides a built-in at equal priority via stable sort). No addon
      contributes previews yet — the field is the replacement seam for the deleted
      runtime registration. Reviewed (arch + react); doc honesty fixes applied.
- [x] **T15 (yes)** DONE — `defineBaseAddon(addon): BaseAddon` exported from
      `@angee/base` (the rendered analog of the SDK's `defineAddon`, type-checks the
      literal). Migrated all 8 manifests off `const x: BaseAddon = {…}` →
      `defineBaseAddon({…})`: iam, operator, storage, knowledge, integrate, agents,
      the notes example addon, and the example `authAddon`. Gate green.
- [x] **T16** DONE — `TopMenu` is now a generic presentational tablist:
      dropped note-specific `DEFAULT_TABS`, the `TOP_TAB_IDS`/`TopMenuTabId`
      literal, and the `DataViewFilter` import (the coupling); `TopMenuTab` is
      `{ id; label; icon? }`; the active tab derives from the passed tabs (first =
      default, unknown `?tab=` falls back). The tab→filter mapping is the consumer
      route's job (reads `?tab=`); **no in-repo consumer wires it yet** — the
      framework keeps only the strip + query state. Storybook fixture made generic.
      (Reviewer noted prefer-deletion is a live option if no consumer arrives.)
- [x] **T17** Forms onto the DSL/registry — DONE (all 4 sub-items below):
      - [x] **drive/vault relation-create via `forms:`** — DONE for **Vault**:
        extracted the inline `VAULT_CREATE_FIELDS` into `knowledge/web/.../views/
        vault-form.tsx` (declarative `<Field>` override) + registered
        `forms: { Vault }` in the manifest; `RelationCreateConfig.fields` made
        optional (FormView's `useFormOverride` supersedes it on create); the wiki
        picker now does `create={{ model: "Vault" }}`. **Drive = LEAVE-SEPARATE**:
        its `backend` select options are runtime-fetched (admin catalogue), which a
        static module-scope override can't carry — kept as a passed `fields` with a
        comment noting why.
      - [x] **replace inline `<form><label>` (iam/operator)** — DONE: both
        hand-rolled `<label className="grid/flex…">{text}<Control/></label>` blocks
        now route through the existing base `FieldRoot`/`FieldLabel` primitive (the
        owner of the stacked label-over-control shape; the same one
        `UsernamePasswordForm` uses — no new `LabeledInput` needed). iam
        `OverviewPage` grant composer: Selects label via `FieldLabel`
        `nativeLabel={false} render={<span/>}` + `aria-labelledby` (button-trigger
        idiom), the truncation hint via `FieldDescription`. operator
        `SecretsSection`: Inputs via `FieldLabel htmlFor`/`Input id` (native-label
        idiom), muted label preserved via `className`. Accessible names preserved
        (vitest + e2e `getByLabel` still resolve).
      - [x] **`Notebook.Tab` onto `PAGE_ELEMENT_SLOT`** — DONE: `Tab` was a
        bespoke `$$notebookTab` marker + `Children.toArray().filter()`. It now joins
        the page-element family — new `views/page/Tab.tsx` (`[PAGE_ELEMENT_SLOT]:
        "tab"`, mirroring `Group.tsx`), `parsePageTabs` in `views/page/index.ts`
        (cached, fragment-flattening, unique-id assertion), and `Notebook` consumes
        it. So an addon can export reusable `<Tab>` constants. Review-driven:
        `TabDescriptor = TabProps` (alias, cf. `ActionDescriptor`); `Tab` sourced
        from `./page` in the barrel (one home, with its siblings); +2 tests
        (fragment-flatten, dup-id throw). Notebook still has no live consumer.
        FOLLOW-UP (tracked, family-wide, out of T17 scope): the per-element
        `*_SLOT` symbols (`FIELD_SLOT`/`GROUP_SLOT`/`ACTION_SLOT`/`COLUMN_SLOT`/new
        `TAB_SLOT`) are write-only — assigned but never read (only
        `PAGE_ELEMENT_SLOT` discriminates). `Tab` keeps `TAB_SLOT` for family
        consistency; a separate slice should delete all five together.
      - [x] **make `FormView` Star/Share host-provided + token** — DONE: deleted
        the hardcoded `RecordChromeButtons` stub (with its `text-amber-500`) from
        base. Base now exposes `FORM_VIEW_RECORD_CHROME_SLOT` (a named slot, the
        AUTH_LOGIN_*_SLOT pattern) rendered via a promoted shared `SlotOutlet`
        (`lib/slot-outlet.tsx` — `SlotOutlet`/`slotEntriesHaveContent`/`slotNode`
        lifted out of `LoginPage`, which now consumes the owner). The notes example
        addon contributes the star/share at build time via `slots: [{ slot:
        FORM_VIEW_RECORD_CHROME_SLOT, … }]` (presentational seam demo), with the
        star on the `text-warning-text` token (two-axis), so the notes e2e
        star/share still resolve. Review-driven: base slot docstring made value-
        neutral (no product styling); RecordChrome docstring made honest about
        being an unwired seam demo.
- [x] **T2b** DONE — `UserMenu` rebuilt on the `DropdownMenu` primitive: native
      `role="menu"`/`menuitem` + arrow-key nav (was a popover faking the roles on a
      plain button). Dropped the manual roles, the controlled-open state, and the
      hand-rolled menuitem styling; the menu auto-closes on select. E2e locators
      (user-menu button, sign-out menuitem) unchanged and now hit native roles.

## Phase 7 — Polish (Tier 4)

- [ ] `pagerVariants`/`treeVariants`; replace `Tree.iconColor:string` with
      token-backed `iconTone` (`ToneName`).
- [ ] Standardize default `size` (switch/toggle-group `sm`→`md` or document);
      unify icon-size spelling (`iconSm/Md/Lg` vs `icon/icon-sm`); reconcile
      `badge.shape` vs `chip.shape` defaults.
- [x] Class-merge: `.filter(Boolean).join(" ")` → `cn()` in `DataPage`/`GraphView`
      (BrandButton's is an aria-id join, left alone).
- [ ] Pick one primitive namespace-export convention (Select/Tooltip split).
- [ ] Delete the write-only page-element `*_SLOT` symbols
      (`FIELD_SLOT`/`GROUP_SLOT`/`ACTION_SLOT`/`COLUMN_SLOT`/`TAB_SLOT`): each is
      assigned `[X_SLOT]: true` on its marker but never read — only
      `PAGE_ELEMENT_SLOT` discriminates. Drop all five together (surfaced during
      T17's Notebook→PAGE_ELEMENT_SLOT review).
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
- [~] Encode the new rules in `docs/frontend/guidelines.md` — DONE for the landed
      owners: two-axis color (`tone`×`variant`), i18n-commit (`useBaseT`/`use<Addon>T`
      on `useNamespaceT`, prop-default coalesce), `defineBaseAddon`, build-time
      composition incl. previews, icon-registry-only (`<Glyph>`, no raw lucide),
      and **forms-DSL specifics (T17)**: `forms:` makes `RelationPicker.create`
      `{model}`-only; `FieldRoot`/`FieldLabel` is the labeled-control owner (native
      `htmlFor`/`id` vs button-trigger `nativeLabel={false}`+`aria-labelledby`);
      base exposes `FORM_VIEW_RECORD_CHROME_SLOT` for host-provided record chrome.
      Still to add when it lands: one state-surface API (T4). `docs/stack.md`
      unchanged (no new libraries — these are Angee patterns).

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
