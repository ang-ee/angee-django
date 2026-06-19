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
      `VcsBridgesPage` (+ confirm agents `InferencePage` matches). Kills the
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
- [x] Move `DEFAULT_STATE_TONE_VALUES` / `stateToneFromValue` business semantics
      OUT of base → explicit caller mapping (SDL metadata carries no tone). DONE:
      `stateToneFromValue(value, buckets)` is now a pure mechanism (`buckets`
      required, no baked product default; iterates `TONES` so it can't return a
      non-`Tone` key). The status vocabulary moved ONTO the `statusBadge` widget
      as `STATUS_BADGE_TONES` (the object whose job is "show a colored status"),
      overridable per-caller via `<Column tone>` threaded through
      `ColumnDescriptor.tone` → `WidgetField.tone`. The widget's override lookup is
      exact-case (matching the existing `cellContent`/`BoardView` `column.tone`
      readers), then layers its convention. Behavior-preserving for all 8 live
      consumers (none pass a tone map → identical colors). +5 widget tests.
      FOLLOW-UP (Phase 7, surfaced by review): `column.tone[label] ?? "neutral"` is
      now spelled in 2 plain-cell readers (`ListInternals.cellContent`,
      `BoardView.laneDotTone`) — a one-owner `columnTone(value, map)` helper could
      converge them, but the widget can't import `views/page` (dep direction), so a
      cross-layer move is its own slice.
      DONE (Phase 7): added `columnTone(column, value): Tone | undefined` beside
      `ColumnDescriptor` (`views/page/Column.tsx`) — the descriptor answers about
      its own value→tone vocabulary (nullish value → empty label, else
      `?? "neutral"`, `undefined` when no tone map). Both readers are in `views/*`
      (not `widgets/*`), so the dep direction is fine; `cellContent` and
      `laneDotTone` route through it (laneDotTone's redundant `!column?.tone` guard
      collapses into the helper's `undefined` return). +4 unit tests. The
      `list-view-utils` `Object.keys(column.tone)` filter-option enumeration stays
      separate (different intent — listing keys, not a value lookup).

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

- [x] Align prop vocab across the state surfaces. DONE (scoped): the TITLED
      surfaces share `{title, description, icon?, actions?}` — `ErrorBanner.message`
      → `description` (2 consumers + story), and `EmptyState`'s dead `body` alias
      deleted (no consumers). The SINGLE-LINE surfaces LEAVE-SEPARATE (different
      intent): `InlineEmpty.label` and `LoadingPanel.message` are title-less
      one-slot surfaces — `label`/`message` read naturally there, and forcing a
      title-companion `description` onto a title-less block is semantically worse.
      Also collapsed the last mixed `LoadingPanel`-OR-`EmptyState` wrapper
      (StoragePage:334) now that both self-center (`LoadingPanel` `h-full` root,
      `<EmptyState fill>`). Encoded the rule in `docs/frontend/guidelines.md`.
      (Pre-existing, out of scope: `FormView`'s `"Save failed"` ErrorBanner title is
      hardcoded English — FormView has no `useBaseT`; a T6 i18n gap, not this slice.)
- [x] Gallery/Timeline/Tree now render an empty state (was blank): `emptyMessage`
      prop (default "No records.") + centered empty render on each; `RowsListView`
      threads `emptyMessage` into its grid-mode `GalleryView`.
- [~] `CollectionStatus`/`DataViewFrame` — did the high-value clean part,
      LEFT the full frame SEPARATE. DONE: extracted `ListEmpty` (the centered
      full-height muted-text empty body) in `ListInternals` — was byte-identical in
      Gallery + Timeline, near-identical in Tree (Tree passes its `min-h-0 p-8`
      via className); all three route through it now. LEAVE-SEPARATE: the full
      `CollectionStatus`/`DataViewFrame` frame — the renderers' status/footer
      contexts genuinely differ (flat virtualized table with a measure footer vs
      grouped tristate with per-group loaders/pagers vs board lane-cell empty vs
      card grid), so one frame would over-parameterize delicate code (cf. the prior
      GroupedList close-read in Phase 5). `fetching`/`error`/`emptyMessage` are
      already threaded through the renderer contracts; Board's lane-cell empty stays
      its own (column context, not a full-pane center).
- [x] Extract the `Spinner + "Loading…"` blocks into `ListLoadingFooter` (footer
      div, was ×3: ListView/RowsListView/GroupedList) + `ListLoadingInline` (span,
      was ×2: GroupedList) in `ListInternals`; dropped 3 now-unused Spinner imports.
- [x] Delete `Chatter`'s local `EmptyState`; use the shared fragment. DONE: the
      file-local `EmptyState` (a bare `min-h-48` block that hardcoded the `agent`
      icon for ALL 3 default tabs) is gone; `defaultTabs` routes to the shared
      fragment with per-tab icons (agent/comments/activity — fixes the wrong-icon
      bug) + `description` + `className="min-h-48 p-4"`, matching how the NotePage
      example already builds its Chatter tabs.
- [x] Add a centered/full-height variant to `EmptyState`; collapse the
      `grid place-content-center` wrapper (×7 storage/knowledge). DONE: `fill?`
      prop wraps the panel in `grid min-h-full w-full flex-1 place-content-center`
      (`min-h-full` not `h-full`, so a flex-col sibling can't force overflow; the
      Card keeps its `min-h-64` intrinsic size, so it's byte-equivalent to the old
      host wrappers — a centered modest card, not a stretched one). Collapsed all 7
      wrappers: StoragePage ×2, KnowledgePage ×3, PageEditor ×1, BacklinksPanel ×1.
      +`Fill` story. FOLLOW-UP: StoragePage:334 + the KnowledgePage detail slot host
      a mixed `LoadingPanel`-OR-`EmptyState` wrapper — they only collapse once
      `LoadingPanel` also gains a `fill` prop (do in the prop-vocab slice).
- [x] Preview renderers: have `useFileText`/`<FileText>` own loading/error so each
      renderer describes only its happy path. DONE: new `FileText` render-prop
      component (`<FileText url>{(text) => happyPath}</FileText>`) owns the
      `useFileText` fetch + the `LoadingPanel`/`EmptyState` guard (was byte-identical
      in TextPreview + MarkdownPreview); both renderers now describe only the happy
      path. Behavior-preserving (review-confirmed clean). DONE (final pass): the
      preview surface is now i18n'd — `builtins` (`FileText` loading/error,
      `FallbackPreview` no-inline) + `PreviewPane` (no-preview empty + Suspense
      loading; gained its first `useBaseT`) route through `preview.*` keys in
      `enBaseMessages`. `FormView`'s "Save failed" ErrorBanner title also routed
      (`form.saveFailed`, FormView gained `useBaseT`). react-review clean.

## Phase 3 — Find-the-owner (T8) + SDK/no-owner lifts (T10)

### T8 — move shape-sniffing onto its owner
- [x] `string|ReactNode` icon adapter → `renderGlyph(icon)` in `chrome/Glyph.tsx`;
      removed the 8 byte-identical local `render<X>Icon` helpers (EmptyState,
      InlineEmpty, MetricStrip, MetricGrid, MiniCard, SurfaceHeader, RecordHeader,
      CollectionHeader) and dropped their now-unused `Glyph` imports.
- [x] PARTIAL — moved the genuine structural reads onto their SDK owners:
      • `RevisionsTab.revisionSnapshot` → `revisionSnapshot(revision)` in
        `resource-result.ts` beside `ResourceRevision` (the revision-row owner);
        the hardcoded meta set is now derived from one `REVISION_ENVELOPE_FIELDS`
        declaration (review fix: was stated 3× in the file).
      • `AggregatePanel` `${field}Id` guess → `bucketKey(bucket, dimension)` in
        `aggregates.ts` beside `dimensionKey`. The `${field}Id` fallback was dead
        (the document selects `key { <dimensionKey> }`, so a bucket only ever
        carries the value under the dimension's key) — deleted, not carried over.
      • `recordSubtitleParts`' relay-id `atob` decode → `relayGlobalIdSuffix(value)`
        in `selection.ts` beside `toRelayGlobalId` (one relay boundary, both
        directions owned). +unit tests for all three.
      LEAVE-SEPARATE: `recordSubtitleParts`' `createdAt`/`updatedAt`/`wordCount`
      field-name sniffing + Intl date/word-count formatting stays in base FormView
      — it is presentation/humanization (the SDK stays structural), `wordCount` is
      product-flavored, and metadata does not classify created/updated/words
      semantics, so moving the names would not cleanly help.
- [x] PARTIAL — moved the field-SHAPE decoders to the `FieldDescriptor` owner;
      kept the form-SUBMIT/draft helpers in FormView (different intent):
      • `widgetId` → `fieldWidgetId(field)` and `isRelationIdField(field)` moved to
        `views/page/Field.tsx` (where `FieldDescriptor` is defined) + exported via the
        page barrel. They are pure field-shape facts ("which widget does this field
        resolve to" / "is it a `many2one` relation-id picker → `<name>.id` selection
        path"), so the descriptor answers them about itself; FormView's 4+3 call
        sites now route through the owner. +unit tests beside the owner. Review fix:
        preserved the original TRUTHY guard (`widget || kind || "text"`, not `??`) so
        an empty `widget` string still falls through to `kind`; +edge tests.
      • `emptyValue(field)` / `hasOptionValue(field)` LEAVE-SEPARATE: they encode
        FormView's draft-seed + submit-normalization semantics (date→null,
        tagInput→[], switch→false, json→{}; "don't submit an empty select/relation"),
        entangled with the mutation/baseline logic — not field identity. They now
        build on the owner's `fieldWidgetId`.
      • Follow-ups (out of scope, tracked in Phase 7): `isLongTextField`'s
        `field.kind === "textarea"` fallback + `gridFieldClass`/the statusbar filter
        still read `field.widget` raw — fold through `fieldWidgetId`. And the two
        divergent option-widget inventories (`hasOptionValue` vs
        `model-metadata-defaults` `ENUM_OPTION_WIDGETS`) could converge on the owner.
- [x] option `value→label` resolver → one `optionLabel(options, value)` owner in
      `widgets/types.ts` (beside `WidgetOption`); routed select, combobox, many2one,
      statusBadge, many2many (its local copy deleted). Left separate (different
      intent): ownerCell (object value), RelationField (finds the option object),
      themePicker (local list + "System" fallback), booleanBadge (boolean match).
- [x] `isLookupOperator` → exported from `data-view-model` (the owner of
      `DATA_VIEW_LOOKUP_OPERATORS`); `list-view-utils` dropped its byte-identical
      14-operator copy and imports the owner's guard (derived from the `as const`
      array, so it can't drift). `Filter` ALREADY owns the text-filter
      target/predicate (`textTerm`/`withTextTerm` default to
      `DEFAULT_TEXT_FILTER_FIELD`; `list-view-utils` `textFilterValue`/
      `nextTextFilter` delegate to it) — nothing to move. `matchesClientLookup`
      (data-view-surface) LEFT SEPARATE: it is the client-side row-matching
      *predicate* for `useRowsDataViewSurface` (a different concern from operator
      membership), not a duplicate of the operator list.
- [x] `AppChooser.itemMatchesPath` / `TopMenu` active logic → DONE: extracted
      `pathMatchesTarget(pathname, target)` (the one path-match predicate, was
      duplicated in `ChromeMenuNode.matchesPath` + `AppChooser.itemMatchesPath`)
      and added `isActive(pathname)`/`hasActiveDescendant(pathname)` to
      `ChromeMenuNode` (the owner); routed `TopMenu` (dropped `menuItemIsActive`)
      and `AppChooser` through them. Behavior-preserving; +2 menu-tree tests.
- [x] PARTIAL — `enumValueLabel` casing DONE; `recordRepresentationFor`
      LEAVE-SEPARATE:
      • `enumValueLabel` humanization (lowercase+title-case) was a duplicate of
        base's existing `statusLabel(value) = titleCase(value.toLowerCase())`
        (verified output-identical for all enum inputs). Moved it out of the SDK:
        `ModelEnumValueMetadata` now carries the STRUCTURAL `{ value, description? }`
        (the SDL-authored description, no derived label); the SDK no longer
        humanizes. Base gained ONE `enumValueLabel(meta) = meta.description ??
        statusLabel(meta.value)` beside `statusLabel` (the humanization owner);
        the 3 consumers (`enumOptions`, `enumLabelFromMetadata`, integrate
        `VcsBridgesPage`) derive through it. Exported `enumValueLabel` +
        `statusLabel` from `@angee/base` (review fix: a public enum-label helper
        whose delegate stayed private was an incoherent seam). Tests updated to the
        structural shape.
      • `recordRepresentationFor` LEAVE-SEPARATE: it selects which FIELD NAME
        represents a record (title/name/.../first String scalar) and returns a
        field name, not a formatted string — consumed as `labelField`/title-field/
        representation-field. That is STRUCTURAL (a model-shape inference), so it
        stays in the SDK.
      • Follow-up (out of scope, noted): `ModelFieldMetadata.label` still carries
        the trimmed SDL field description under the name `label` (base's `fieldLabel`
        already owns the `titleCase(name)` humanization fallback). The split is fine;
        only the SDK field name (`label` vs `description`) is unaligned with the
        enum convention — a naming-alignment slice, larger surface, deferred.

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
- [x] SDK mutation hooks → one `useDocumentMutation`; collapse
      `useStableMeasures` into a generic `useStableValue`. DONE: new
      `document-mutation.ts` `useDocumentMutation(document)` — the write counterpart
      of the internal `useDocumentQuery` read seam — owns urql `useMutation` +
      throw-on-GraphQL-error + the `{fetching, error}` shape and exposes a stable
      `execute(variables)`; the 4 mutation hooks (`useAuthoredMutation`,
      `useLoginWithPassword`, `useLogout`, `useResourceMutation`) route through it,
      each keeping its own post-success effects (client reset / `invalidateModels`)
      and data shaping on the caller. `stable-deps.ts` gained one generic
      `useStableValue(value, fallback)` (the structural-equality memo owner);
      `useStableVariables`/`useStableMeasures` are thin defaults over it.
      `useStableArray` left separate (cheaper join key, different intent). Both new
      seams stay internal (not exported), matching their siblings. Behavior-
      preserving (react + arch review clean; +`useResourceMutation` data type
      tightened `any`→`unknown`).

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

- [x] `pagerVariants`/`treeVariants`; replace `Tree.iconColor:string` with
      token-backed `iconTone` (`Tone`). DONE: extracted the hand-rolled tree row
      consts (`ROW_BASE`/`ROW_ACTIVE`/`ROW_DROP_TARGET` + manual `cn`) into a
      `treeVariants` `tv` recipe (boolean `active`/`dropTarget`), and the pager
      label consts into `pagerVariants` (`label: "button" | "span"`, default
      `span`); byte-identical class output (review-verified). `TreeNode.iconColor`
      (raw inline `style={{color}}`) → `iconTone?: Tone`, rendered via a new
      `toneGlyph(tone)` accessor in `tones.ts` — a `[&_.glyph]:text-*-text!`
      glyph-scoped tint that actually wins over the row's own `[&_.glyph]:text-*`
      (the old span tint was silently defeated by specificity; lucide glyphs stroke
      `currentColor`). +tree.test.tsx (tint + recipe). No `iconColor` consumers
      existed, so the prop swap is safe.
- [x] Standardize default `size` (switch/toggle-group `sm`→`md` or document);
      unify icon-size spelling (`iconSm/Md/Lg` vs `icon/icon-sm`); reconcile
      `badge.shape` vs `chip.shape` defaults. DONE (behavior-aware):
      - **icon-size spelling** — DID align: `toggle`'s `icon`/`icon-sm` size keys
        renamed to `iconMd`/`iconSm` to match the Button convention (`iconSm`/
        `iconMd`/`iconLg`); the lone consumer (`Toggle.stories` `size="icon"`) →
        `iconMd` (byte-equivalent: old `icon` = `size-icon-btn-md`). One spelling
        now. typecheck base + storybook green.
      - **switch/toggle-group default `sm` → md** — LEAVE-SEPARATE (document, a flip
        regresses): a default change is a visual change. `Switch` default `sm` has 2
        consumers, neither passes `size` — flipping enlarges every switch with no
        requester; `sm` is the form-control size. `ToggleGroup` default `sm` is a
        compact segmented control; `Toggle` default `md` is a standalone
        button-like — different intents, the differing defaults are justified, not
        drift.
      - **`badge.shape:"rounded"` vs `chip.shape:"pill"`** — LEAVE-SEPARATE: badge
        (status/count) and chip (removable token) are the established
        different-intent pair (T9); the shape defaults follow that intent.
      (NOTE for the docs agent: record the "icon-size keys are `iconSm`/`iconMd`/
      `iconLg` across recipes" convention + the size-default rationale in
      `docs/frontend/guidelines.md`.)
- [x] Class-merge: `.filter(Boolean).join(" ")` → `cn()` in `DataPage`/`GraphView`
      (BrandButton's is an aria-id join, left alone).
- [x] Pick one primitive namespace-export convention (Select/Tooltip split).
      DONE (scoped + delete-the-wart): the convention is coherent once stated — a
      primitive exports flat per-part consts (universal); a compound primitive
      exposes a bare-name parts-namespace object (`Dialog`/`AlertDialog`/`Popover`,
      all actively consumed as `Dialog.Root` etc.); a primitive that ships a
      composed convenience component takes the bare name for it (`Select`,
      `Tooltip`) and exposes parts under a `*Primitive` suffix ONLY where consumers
      compound them (`SelectPrimitive`, used by `combobox`). The lone wart was a
      DEAD `TooltipPrimitive` (zero consumers) — a `*Primitive` namespace on a
      tooltip nobody compounds. Deleted it (prefer-deletion). Every surviving
      namespace object is now one real consumers use. NOTE for the docs agent:
      encode this convention in `docs/frontend/guidelines.md` (this slice avoided
      docs per the handoff split).
- [x] Route the remaining raw widget-shape reads in FormView through the
      `fieldWidgetId` owner. DONE (the genuine folds):
      - `gridFieldClass`: `field.widget === "tagInput"` → `fieldWidgetId(field) ===
        "tagInput"`.
      - statusbar filter: `field.widget === "statusbar"` → `fieldWidgetId(field) ===
        "statusbar"` ("statusbar" is a widget, never an SDL kind, so identical
        behavior + the correct widget-overrides-kind resolution).
      - `isLongTextField`: deleted the redundant `field.kind === "textarea"` tail —
        `fieldWidgetId` already returns `kind` when no `widget` is set, so
        `id === "textarea"` subsumes it; the separate read would only (wrongly) fire
        when an explicit widget overrides the kind, against `fieldWidgetId`'s
        widget-wins contract.
      LEAVE-SEPARATE: **`hasOptionValue` (FormView) vs `ENUM_OPTION_WIDGETS`
      (`model-metadata-defaults`)** — different SETS and different INTENT, not one
      predicate. `hasOptionValue` = {select, many2one, statusbar} ∪ kind {select,
      selection}, gated on inline `options`; its job is submit-emptiness ("would `""`
      mean an unselected option?") and it deliberately includes the `many2one`
      relation and checks widget OR kind independently (a widget=text,kind=select
      field still counts). `ENUM_OPTION_WIDGETS` = {select, selection, statusbar,
      statusBadge, ribbon}; its job is METADATA-DEFAULTING ("auto-attach SDL enum
      options"), so it includes the display widgets `statusBadge`/`ribbon` and
      excludes `many2one`. Folding them would be wrong (DRY: different intent →
      separate). `hasOptionValue`'s widget-OR-kind independence is also load-bearing
      (routing through `fieldWidgetId`'s widget-XOR-kind would change the
      widget=text,kind=select case), so it keeps its explicit reads.
- [x] Delete the write-only page-element `*_SLOT` symbols
      (`FIELD_SLOT`/`GROUP_SLOT`/`ACTION_SLOT`/`COLUMN_SLOT`/`TAB_SLOT`). DONE:
      grep-confirmed each was assigned `[X_SLOT]: true` on its marker + exported
      from the page barrel but never READ anywhere (only `PAGE_ELEMENT_SLOT` and
      its `PageElementKind` string discriminate, via `pageElementProps`). Deleted
      the 5 `Symbol.for(...)` declarations, the 5 marker assignments, and the 5
      barrel re-exports together; not re-exported above the page barrel, so no
      external break. typecheck + test green.
- [x] Storybook cleanup DONE (Phase 7 final pass; react+arch review clean):
      • **Dead `args`/`argTypes`** removed from 21 meta-level + 3 story-level stories
        where every story self-renders (`render: () =>`). The agent caught 5 FALSE
        POSITIVES (Accordion/Collapsible/Drawer/SelectionBar/ToggleGroup have
        `render: ({…args}) =>` consumers — kept). Where removing meta args broke the
        type (component has a required prop → `StoryObj<typeof meta>` still demands
        it), the self-render stories were typed as bare `StoryObj` (kept `component:`
        for autodocs). New convention recorded in guidelines Pitfalls.
      • **`runtime-fixtures` owner** (`stories/runtime-fixtures.tsx`, mirroring
        `chrome-fixtures.tsx`): `jsonResponse` + `storySchema(fetch)` (CSRF probe
        uniform) + `<RuntimeFixture>` (ModalsHost→GraphQLClientProvider→
        AppRuntimeProvider{icons,slots,widgets}); ListView + FormView consume it
        (local copies deleted). `jsonResponse` NOT lifted to `@angee/base/testing`
        (arch-review: that module's hermetic fixed responders are a different intent).
        ScalarWidgets LEFT separate (only needs `AppRuntimeProvider{icons}`, no fetch).
      • **Redundant nested `ToastProvider`** dropped from Toast.stories (global
        preview decorator already provides it; `ToastCanvas` is now a layout div).
      • **Taxonomy**: `Feedback/Toast` → `Primitives/Toast` (beside Alert), so no
        1-member Feedback group; `preview.tsx` storySort dropped Feedback, added the
        existing Foundations, reordered Page (parts) before Layouts (compositions).
        Page-vs-Layouts split KEPT (semantically real: parts vs full-page comps).
        Forms/Foundations/Widgets 1-member groups LEFT (deliberate top-level pages,
        not called out).
      • **Hero color literals**: MarketingHero + PublicShell stories `text-white`/
        `bg-white/X`/`border-white/X` → `n-0` token utilities (matching real
        LoginPage/PublicShell). The bespoke `bg-[linear-gradient(…#hex)]` hero
        backdrops LEFT (real LoginPage uses the same; no token for an art gradient).
      • Main's touched stories verified consistent post-merge (StatusIcon/Toast/
        SurfacePanel/chrome-fixtures).
- [x] `useOperatorSnapshot` `want*` flags → one `SECTION_KEYS` table; principled
      List/Form declaration placement in agents; collapse two translators
      (`useT`/`translateWithFallback`). (All three sub-items resolved below:
      SECTION_KEYS DONE; the two translators + agents List/Form = LEAVE.)
      - [x] **`SECTION_KEYS` table** DONE: the `want*` mapping was spelled three
        times (8 `const want* = sections.X ?? false`, the 8-key variables object,
        the 8-entry memo deps). Collapsed onto one `SNAPSHOT_SECTIONS` table (the
        pane keys, `satisfies (keyof OperatorSnapshotSections)[]`) + a
        `wantVariable(section)` deriving the `$want<Pane>` toggle; `variables` is
        built from the table and memoized on a one-string `sectionsKey` signature.
        `SnapshotVariables` is `Record<WantVariable, boolean>` (a template-literal
        mapped key `want${Capitalize<keyof OperatorSnapshotSections>}`), so the type
        still asserts all 8 toggles (review fix: don't relax to open `string`). API +
        behavior preserved (typecheck + 13 operator tests green; memo equivalence
        review-verified).
      - [ ] **collapse `useT`/`translateWithFallback`** — LEAVE-SEPARATE (scoped):
        they have distinct roles. `translateWithFallback(t, fallback, key, vars)`
        (SDK `i18n.ts`) is the pure function — host `t` first, then the addon's own
        `fallback` bundle, then the raw key; it is the shared mechanism that
        `useNamespaceT` (and thus `useBaseT`/every `use<Addon>T`) is built on, and
        it is independently exported + unit-tested. `useT(namespace)` (SDK
        `runtime.ts`) is a thinner React hook that reads only the runtime bundle for
        a namespace (no static fallback) — a different contract. Not redundant;
        `useNamespaceT` already unified the fallback-bearing path.
      - [x] **principled List/Form declaration placement in agents** —
        ALREADY-PRINCIPLED / LEAVE (scoped): each agents page (`AgentsPage`,
        `McpServersPage`/`McpToolsPage`, `SkillsPage`) declares its own
        `<DataPage><List/><Form/></DataPage>` inline — the idiomatic, established
        `DataPage` pattern across every addon, where the page IS the declaration
        owner. The only shared shape (`AgentsPage`/`TemplatesPage`, one model, two
        `isTemplate` tabs) is already factored into a shared `agentDataPage(...)`
        helper. Nothing is duplicated or misplaced: these are full page VIEWS, not
        the relation-picker create-form OVERRIDES that the `forms:` registry owns
        (T17), so there's no registry move to make. No change.

## Exhaustive fixes (deferred — track, don't do yet)

- [ ] **T19** ~12 storybook-only fragments (`FocusPanel`, `ListPanel`,
      `HeroPanel`, `MarketingHero`, `DataLens`, `MetadataPanel`,
      `CollectionHeader`/`SurfaceHeader`/`RecordHeader`, `EditorHeaderBar`,
      `FrameToolbar`, `SectionTabs`): **kept for now** per decision. Revisit per
      fragment on first real consumer — delete vs promote. Note added so they
      aren't mistaken for live API.
- [~] Full sweep for any remaining hard-coded copy / raw lucide / `cn` violations
      after Phases 1–7 land (drift check: `grep` for `from "lucide-react"` outside
      icon-registry, `toLocaleDateString`, `error` tone, `.join(" ")` class merges).
      DONE (final pass): drift greps CLEAN — (a) `lucide-react` outside the registry:
      the only hits are addon `index` files contributing the `icons:` manifest field
      (the icon-registry mechanism for addons), not raw rendering — correct; (b)
      `.filter(Boolean).join(" ")`/`.join(" ")`: no className merges — BrandButton's is
      an aria-id join (already noted), the rest are GraphQL selection/CSS-grid-area/
      error joins; (c) retired `error`/`default` tone: `Kbd tone="default"` is its own
      key-surface vocabulary (false positive), all `"error"` hits are state
      discriminants / value-bucket keys; (d) `stateToneFromValue` calls all pass
      `buckets`. Merged-in i18n gaps fixed: iam ProvidersPage/CredentialsPage/
      OAuthConnectCallbackPage, agents AgentProvisioning/SourcesPage (Area A.1). STILL
      DONE (straggler slice): the pre-existing T6 stragglers are now routed —
      base (`useBaseT` + `enBaseMessages`): combobox (search/no-options/widget-label
      fallbacks), select (widget-label fallback), createApp `RequireAuth`
      (loading-workspace/redirecting), DeletePreviewDialog (title+count/blockers/
      Cancel/Delete/Rows-affected/Deleted-Updated-Blocked), markdown editor toolbar
      (bold/italic/code/lists/quote/link + source/preview mode + placeholder),
      AggregatePanel (loading/no-data), ListInternals (Total + measure aria + the
      two `Select row` checkbox aria-labels — review-driven), RelationField
      (placeholder/searchPlaceholder undefined+coalesce, No-matches, Create-query).
      iam (`useIamT`): UsersPage + ExternalAccountsPage (module-const forms moved
      into component bodies; revoke reuses `iam.revoke`). storage (`useStorageT`):
      StorageSettingsPage + FileDetail field/group labels. LEAVE-SEPARATE with
      reason: (a) `credential-form.tsx` is a statically-parsed `forms:` registry
      override (never rendered as a component → a hook can't reach its labels;
      i18n'ing it needs a render-time form registry, an architecture change); (b)
      addon menu/route `label:` declarations are a consistent plain-English boundary
      — NO addon/base/example routes menu labels through `t()` (chrome-resolved
      manifest data, not in-component copy); (c) DataLens/RailPanel/SurfaceHeader/
      MetadataPanel copy = T19 storybook-only fragments. react-review clean.
- [x] Encode the new rules in `docs/frontend/guidelines.md` — DONE for all landed
      owners: two-axis color (`tone`×`variant`), i18n-commit (`useBaseT`/`use<Addon>T`
      on `useNamespaceT`, prop-default coalesce), `defineBaseAddon`, build-time
      composition incl. previews, icon-registry (`<Glyph>`), state-surface vocab
      (T4, the titled-vs-single-line surfaces + `EmptyState fill` + renderer-owns-
      loading/error), and **forms-DSL specifics (T17)**: `forms:` makes
      `RelationPicker.create` `{model}`-only; `FieldRoot`/`FieldLabel` is the
      labeled-control owner; base exposes `FORM_VIEW_RECORD_CHROME_SLOT`.
      FINAL-PASS additions: (a) the i18n rule now records the two plain-English
      boundaries (declarative manifest menu/route `label:`; the statically-parsed
      `forms:` override); (b) the icon rule made precise (a component never imports
      lucide; an addon contributes via the manifest `icons:` field, the registry
      seam); (c) new rules for the icon-size key spelling (`iconSm/iconMd/iconLg`) +
      size-default-is-a-contract, and the primitive export/namespace convention
      (flat per-part consts; bare-name parts-namespace for compounds; `*Primitive`
      suffix only where compounded); (d) a Pitfalls entry on storybook dead-args
      (`StoryObj<typeof meta>` vs bare `StoryObj`) + the `runtime-fixtures` owner +
      preview-decorator providers. `docs/stack.md` unchanged (no new libraries —
      all Angee patterns).

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
