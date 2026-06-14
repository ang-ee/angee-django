# React Consistency & DRY Cleanup — Cross-Engine Audit Report

**Date:** 2026-06-14
**Goal:** make the Angee frontend fully consistent and DRY — both code-wise and
UX-wise — by finding "similar but not consistent / not DRY / not architecturally
clean" code and prescribing the cleanup.

**Workspace:** this cleanup runs on `workspace/react-consistency` (branched from
`main`). The exhaustive, trackable task list is `react-consistency-todo.md`
beside this file; this report is the rationale + evidence.

## Decisions (locked 2026-06-14)

1. **i18n (T6) → COMMIT.** Route base default labels through `useBaseT()` with an
   expanded `base` bundle, and give every addon its own i18next namespace. No
   hard-coded user-facing copy in framework or addon code going forward.
2. **Semantic color (T1) → OPTION C: two orthogonal axes (the Radix Themes
   model).** Split *which palette* from *how it's filled*:
   - `tone` (a.k.a. `color`) = the **semantic/brand palette** (`brand`, `neutral`,
     `info`, `success`, `warning`, `danger`, …). One value vocabulary everywhere;
     negative state is **`danger`** (retire Toast's `error`).
   - `variant` = **fill emphasis** (`solid` | `soft` | `surface` | `outline` |
     `ghost`). This directly resolves the root conflation — today Badge's
     `variant` means *color* while Button's `variant` means *fill*.
   - Components grow toward two props but only declare the axis they need; a
     primitive that is single-tone keeps just `variant`, a status pill takes both.
     `lib/tones.ts` becomes the (palette × fill) → class matrix owner.
   - Accept the larger API change; it's the cleanest, composable separation.
3. **`defineBaseAddon` (T15) → YES.** Export `defineBaseAddon()` from
   `@angee/base` as the single greppable declaration owner for rendered addons;
   migrate the `BaseAddon` object literals onto it.
4. **Speculative fragments (T19) → KEEP for now.** Do not delete the ~12
   storybook-only fragments yet; they are noted in the TODO under "Exhaustive
   fixes (deferred)" for a later deliberate pass (delete vs promote on first real
   consumer).

## Method

Two independent engines (Claude sub-agents + Codex `exec`) audited the **same 11
concern-oriented slices** of the React codebase in parallel, each judged against
the repo's *own* rules (`docs/frontend/guidelines.md`, `docs/stack.md`,
`CLAUDE.md`) — not generic taste. Each analyst also emitted an "idiom inventory"
(how its slice does dialogs / forms / lists / loading-states / styling / icons /
data-fetching / i18n) so cross-slice "same concern, done differently" patterns
surface at synthesis.

- Slices: `views-collections`, `views-record`, `ui-primitives`, `widgets`,
  `chrome-shell`, `composition`, `support`, `sdk`, `addon-web-a` (iam+operator),
  `addon-web-b` (storage/knowledge/integrate/agents/notes), `storybook`.
- Raw per-slice findings (full `path:line` detail): `test-results/react-audit/`
  (`ALL-CLAUDE.md`, `ALL-CODEX.md`, `claude/*.md`, `codex/*.md`) — gitignored
  scratch; this report is the self-contained synthesis.
- **Consensus tag** below: ⊕⊕ both engines independently flagged it (high
  confidence); ⊕ one engine, verified plausible against the docs.

The single loudest signal: the codebase has **strong structural consistency**
(forwardRef + `tv` recipe + `Pick`'d variant props + `Object.assign` namespace
is followed almost everywhere; the SDK is clean of UX leakage) but **weak
semantic consistency** — the same *concept* (a tone, a state surface, an icon, a
date, a piece of copy, a labeled control) is expressed several different ways.
The fixes are mostly "pick the one owner that already exists and route everyone
through it," which is squarely the repo's own "find the owner" constitution.

---

## Tier 1 — Systemic, both engines, highest leverage

### T1. The semantic-color axis is named & valued inconsistently — ⊕⊕ `[ux+arch, HIGH]`
One concept ("semantic state color") ships as **four prop names**: `intent`
(Alert, StatusIcon, Toast), `tone` (StatusDot, Slider, ListColumn, chrome menus,
Kbd, BrandButton…), `variant` (Code, Badge, MetricGrid), and numeric `color`
(Avatar). The **value sets diverge** too: negative state is `danger` everywhere
*except* Toast, which uses `error`; the info glyph is `"info"` in Toast vs
`"help"` in Alert/StatusIcon. The storybook surfaces the collision side-by-side.
- **Owner / fix:** `@angee/base` recipes + `lib/tones.ts`. Pick one role-based
  vocabulary (Codex's proposal is sound: `intent` = feedback/status, `tone` =
  surface tint, `variant` = structural shape; drop numeric `color`), one negative
  word (`danger`), one option set; rename the `tailwind-variants` recipes and
  export the option constants so stories/consumers read uniformly. **Highest-
  leverage UX cleanup in the repo** — every consumer copies these prop names.

### T2. `lib/tones.ts` is the tone owner but is both bypassed and overloaded — ⊕⊕ `[code-dry+arch, HIGH]`
- **Bypassed:** `chip`, `alert`, `alert-dialog`, and `chrome/AppChooser.toneClass`
  hand-retype the per-tone `bg-*-soft / text-*-text / border-*` triples that
  `tones[name]` already provides (`badge`/`status-icon` consume it correctly).
- **Overloaded:** `tones.ts` also hard-codes **business semantics** —
  `DEFAULT_STATE_TONE_VALUES` maps domain values (`published`, `approved`,
  `lost`…) to tones, and `stateToneFromValue()` applies it to arbitrary strings.
  Domain knowledge in a base styling primitive.
- **Owner / fix:** route every soft-tone consumer through `tones`; **move** the
  status-value→tone mapping out of base to model/field metadata or an explicit
  caller mapping. Re-export `tones`/`ToneName`/`slot` from `lib/index.ts` (the
  barrel currently omits them, so consumers deep-import).

### T3. The intent→glyph map is duplicated three times — ⊕⊕ `[code-dry, HIGH]`
`TOAST_ICONS` (Toast), `ALERT_ICON_NAMES` (alert), `STATUS_ICON_NAMES`
(status-icon) all hard-code the same status glyphs and drift (`info:"info"` vs
`info:"help"`).
- **Fix:** one exported `INTENT_GLYPHS` next to `tones`, consumed by all three.

### T4. Loading / empty / error states are hand-rolled and inconsistent — ⊕⊕ `[ux+code-dry, HIGH]`
Two compounding problems:
- **(a) The shared state primitives have divergent prop vocabularies:**
  `EmptyState{title,description,icon}`, `InlineEmpty{label,icon}`,
  `LoadingPanel{message}`, `ErrorBanner{message,title,actions}` — same "state
  surface" concept, four prop shapes.
- **(b) Callers fork or hand-roll instead of using them:** the
  `Spinner + "Loading…"` footer copy-pasted 5× across the list shells; `Chatter`
  defines its *own* `EmptyState` while its sibling `RevisionsTab` imports the
  shared one; operator renders bespoke `<TableCell>No services.</TableCell>`;
  preview open-codes the loading/error guard twice; `AggregatePanel` reimplements
  it; storage/knowledge wrap `EmptyState` in a `grid place-content-center` div 7×;
  Gallery/Timeline/Tree render blank when empty; Rows grid mode forwards rows to
  Gallery with no `emptyMessage`.
- **Owner / fix:** align the four state-surface prop names; introduce one
  `CollectionStatus` / `DataViewFrame` (table-row and block variants) and thread
  `fetching`/`error`/`emptyMessage` through every collection renderer contract;
  delete the forks. Give `EmptyState` a centered/full-height variant.

### T5. The icon registry is bypassed by raw `lucide-react` imports — ⊕⊕ `[arch, HIGH — stated pitfall]`
The guideline says generic glyphs live in `chrome/icon-registry` and render via
`<Glyph name>`. Raw imports persist in: `DataToolbar` (~10 icons), `ui/`
(`dialog` X, `input` Search/X, `select` Check/ChevronDown, `checkbox` Check/Minus,
`pager` chevrons), `widgets/` (`markdown`, `tagInput`), `views/` (`ListInternals`,
`RowsListView`, `GroupedList` sort/layout/chevrons; `DataPage` pager chevrons),
`AppRail.RailGlyph` (`CircleHelp` fallback). The two paths even style icons
differently (`[&_svg]` vs the `.glyph` class).
- **Fix:** register the missing names (`filter`, `sliders-horizontal`,
  `grid-2x2`, `chevron-left/right`, `check`, `minus`, `search`, `x`), route all
  through `<Glyph>`, and give `Glyph` an optional `fallbackName` (default
  `"help"`) so the rail stops hand-drawing a fallback.

### T6. i18n is half-applied — commit or remove (architect decision) — ⊕⊕ `[ux/arch, HIGH]`
Only `ui/input` and `feedback/Toast` use `useBaseT()`; the `base` bundle has 3
keys. Everything else hard-codes English: `ModalsHost` ("Confirm"/"Cancel"/
"Done"), all of `auth`, all of `communication`, all chrome copy, every `ui`
default label/aria-string, and **all** addon copy (iam ships no namespace;
operator routes only section titles).
- **Decision required:** either **(a) commit** — route base default labels
  through `useBaseT()` with an expanded base bundle and give every addon a
  namespace; or **(b) drop** the half-measure so the codebase is consistently
  plain English. The stack *names* i18next, so (a) is the documented intent — but
  this is a policy call with real scope, not a mechanical edit.

### T7. Date / relative-time formatting is reimplemented instead of date-fns — ⊕⊕ `[code-dry+ux, HIGH]`
Local `toLocaleDateString` / `new Date()` / `Intl` in the record subtitle,
`storage/file-display`, `knowledge/PageEditor`, plus **two different `parseDate`**
in `ListInternals` (lenient `new Date`) vs `TimelineView` (date-fns `parseISO`).
Visible symptom: the same `updatedAt` renders **"Jun 14"** in a custom list and
**"3 days ago"** in a model-driven list.
- **Fix:** one date-fns-backed `RelativeTime`/datetime widget (export if not
  public); delete the local formatters; one `parseRowDate`.

---

## Tier 2 — Structural duplication & forks (both engines)

### T8. "A function that inspects an object to decide" — the find-the-owner smell, repeated — ⊕⊕ `[arch+code-dry, HIGH]`
The exact anti-pattern the constitution names, in many places:
- `string | ReactNode` icon adapter copied in **8 fragments** → `Glyph` should
  accept the union (or export `renderGlyph`).
- `recordSubtitleParts` sniffs `createdAt`/`created_at` field-name variants and
  `atob`-decodes ids; `AggregatePanel` guesses `${field}Id`; `RevisionsTab`
  iterates `Object.entries` to guess the changed field → **SDK/model metadata**
  should expose subtitle parts, aggregate keys, and changed fields.
- `FormView` decodes field behavior from widget/kind strings (`widgetId`,
  `isRelationIdField`, `hasOptionValue`, `emptyValue`) → the **widget/field
  resolver** should own selection path / empty value / submit normalization.
- option `value → label` resolver reimplemented in **7 widgets** → one helper on
  `WidgetField.options`.
- `matchesClientLookup`/`isLookupOperator` re-list the operator set
  `DATA_VIEW_LOOKUP_OPERATORS` already models; `AppChooser.itemMatchesPath` /
  `TopMenu` active logic re-derive `ChromeMenuNode.matchesPath` → add
  `isActive(pathname)`/`hasActiveDescendant(pathname)` to the tree.
- SDK `model-metadata` owns display heuristics (`enumValueLabel` title-casing,
  `recordRepresentationFor` candidate order) → keep SDK structural; move
  humanization to the rendered binding.

### T9. Near-duplicate components / forked trees → one owner + variants/slots — ⊕⊕ `[code-dry, HIGH]`
- `dropdown-menu` ≈ `context-menu`: identical recipe + ~300 lines of wrappers
  (only delta: a `trigger` slot) → shared `menuVariants` + a
  `createMenuParts(BaseNamespace)` factory.
- The floating "overlay surface" base class is hand-copied across
  popover/nav-menu/dropdown/context/select/tooltip → one `OVERLAY_SURFACE`
  constant (`popover.tsx` already exports `POPUP_BASE`/`MODAL_BASE` for this).
- `ListView` vs `RowsListView` fork the whole data-view shell (provider /
  default-group sync / toolbar / pager / status) → one shell with injected
  surface hooks; `GroupedList` re-fetches and re-tables outside the shared
  surface (and hardcodes leaf page size `20`); row/card/board **activation +
  selection** are forked (BoardView even ignores `selectedIds`/`interactive`) →
  one activation/card primitive owning `href`/`onActivate`/keyboard/selection/DnD.
- Three field/label stacks (`field.tsx` vs `form.tsx` `FormField` vs
  `form-layout.tsx` `FieldRow`) + the required-`*` markup copied 3× → make
  `field.tsx` the single owner; `FieldRow` composes it. Input chrome
  (border/bg/focus/disabled/readOnly) is retyped in textarea/field/number-field/
  select instead of extending `widget-control.ts`'s `widgetControlSurface`.
- `Badge`/`Tag` vs `Chip` are two near-duplicate "pill" primitives with divergent
  defaults → one tag primitive, `tones`-driven, shared size scale.
- Three header fragments (`CollectionHeader`/`SurfaceHeader`/`RecordHeader`) fork
  `PageHeader` → collapse to one with a `tone` token + optional slots.
- `ControlBand` ≈ `Statusline` (identical portal-band context/provider/tri-state;
  docstring even says "Mirrors the control band") → one `createShellPortalSlot`.
- Page chrome + aside plumbing copied across `CanvasPage`/`RecordView`/
  `SplitView`/`HeroPage` → one `PageChrome`/`TwoPaneFrame` owner.
- Operator builds raw `<Table>` with row actions **6×** → one `DaemonResourceTable`
  (or move to `RowsListView`); storage ≈ knowledge duplicate the scoped
  `Explorer`/`TreeView` controller → one `ScopedTreeExplorer` primitive.
- `date` vs `datetime` widgets duplicate calendar/parse; `tagInput` vs `many2many`
  duplicate chip-list rendering → shared date popover helper + a `ChipList`/
  `TokenList` primitive with normalization hooks.
- `GlobalSearch` vs `Spotlight`: **two** command palettes — and the one wired into
  `TopBar` (`GlobalSearch`) is a dead-end (its `onSearch` is wired nowhere; it
  re-hand-rolls `ui/command` + `Kbd`), while the real styled one (`Spotlight`) is
  storybook-only. Both define an identical ⌘K handler + `isTextEntryTarget`, so
  two components race for the shortcut → make `Spotlight`/`ui/command` the single
  owner; delete `GlobalSearch` (or reduce it to a trigger).

### T10. No-owner plumbing helpers duplicated across addons / SDK — ⊕⊕ `[code-dry, MED–HIGH]`
Lift each to `@angee/sdk` (or a base lib):
- `unknown → message` error helper (×5, both addons); `run()` busy-wrapper hook
  (×3 action hooks); relay `global-id` codec (×2, storage+knowledge);
  `ActionResultData`/`IdVariables` redefined per addon; `titleCase` reimplemented
  as `titleLabel`/`stateLabel`; `pageSize={50}` restated at **14** sites (it *is*
  `DEFAULT_PAGE_SIZE`); ⌘K handler + `isTextEntryTarget` (×2); confirm/prompt
  promise-queue machinery (×2 in `ModalsHost`).
- SDK: mutation hooks (`useAuthoredMutation`/`useResourceMutation`/`useLogin…`)
  duplicate execute + error-normalize + side-effects → one `useDocumentMutation`;
  `useStableMeasures` ≈ `useStableVariables`; `createSchemaClients` ≈
  `createSchemaRuntime` and is **dead** outside tests → delete.

---

## Tier 3 — Architecture / correctness (both engines)

### T11. Aggregate hooks bypass the refetch/invalidation registry → stale data — ⊕⊕ `[correctness, HIGH]`
`useResourceAggregate`/`useResourceGroupBy` never call `useRegisterModelRefetch`
nor return `refetch`, so `AggregatePanel`/`GroupedList` go stale on a
`<model>Changed` push or post-write invalidation **while the list beside them
refreshes**. Fix: route all reads through one `useModelDocumentQuery` wrapper that
adds registry registration + `refetch`.

### T12. Action `ok:false` is handled inconsistently → green toast on failure — ⊕⊕ `[correctness/ux, HIGH]`
Every action mutation returns `{ ok, message }`, but integrate handlers
`return result.message` regardless of `ok`, so a business failure
(`{ok:false,message:"GitHub token expired"}`) shows a **success** toast. Only
agents' `InferencePage` checks `ok`. Fix: one `runActionResult` / `Action`
convention — `if (!ok) throw new Error(message); return message`.

### T13. Preview registry is a runtime-mutated global, bypassing build-time composition — ⊕⊕ `[arch, HIGH]`
Previews use a module-global `Map` populated by an import side-effect, last-write-
wins (not fail-fast), needing `clearPreviewProvidersForTest` — violating "compose
at build time; no runtime registration" while `composeAddons` already provides
the build-time channel for widgets/icons/forms/slots. Fix: make previews an addon
contribution folded by `composeAddons`/`createApp`.

### T14. Invalidation-after-write done two ways — ⊕ `[arch, MED]`
integrate uses `useModelInvalidation(model)` (documented); storage/knowledge
thread manual `query.refetch()` closures through every action hook (and miss live
cross-actor `changes()`). Fix: standardize on `useModelInvalidation`.

### T15. Rendered addons have no declaration owner (`defineAddon` gap) — ⊕⊕ but **disputed** `[arch, MED — decision]`
Codex flags `const iam: BaseAddon = {…}` literals as bypassing `defineAddon`;
Claude investigated and concluded the literal **is** the correct current
convention (`defineAddon` is the *headless* `AddonManifest` brand, not for
rendered addons). Both nonetheless expose a real gap: **there is no greppable
`defineBaseAddon` owner** for rendered addons. Recommend exporting
`defineBaseAddon()` from `@angee/base`. *Architect decision needed.*

### T16. Product specifics leaking into framework chrome — ⊕ `[arch, MED]`
`TopMenu` imports `DataViewFilter` and `DEFAULT_TABS` hard-codes "All notes" /
"Starred" / "Archive" — note-specific data living in base chrome. Fix: move
tab→filter to the data-view/route owner; keep `TopMenu` presentation-only.

### T17. Custom forms / record chrome bypass the declarative DSL & registry — ⊕⊕ `[arch, MED]`
Inline `<form><label>` mini-forms in iam/operator; relation-create built as a
local `FieldDescriptor[]` in page code instead of `defineAddon` `forms:`;
`Notebook.Tab` uses a private `$$notebookTab` marker instead of
`PAGE_ELEMENT_SLOT`; `FormView` ships hard-coded `Star`/`Share` record chrome with
a one-off `text-amber-500`. Fix: use the DSL/registry; make star/share
host-provided via a slot/action; one-off color → token.

### T18. Missing Tailwind `@source` for composed addons — ⊕ `[arch/bug, HIGH]`
The host CSS scans base/agents/iam/operator/notes but **not** integrate/storage/
knowledge, whose arbitrary classes (`max-w-[1100px]`, `max-w-[820px]`) silently
fail to generate — the exact stated pitfall. Fix: add `@source` entries for the
three packages.

### T19. Speculative / unused framework fragments (lifted, unearned) — ⊕ `[arch, HIGH]`
~12 fully-styled fragments (`FocusPanel`, `ListPanel`, `HeroPanel`,
`MarketingHero`, `DataLens`, `MetadataPanel`, the three header forks,
`EditorHeaderBar`, `FrameToolbar`, `SectionTabs`) have **zero** non-storybook
consumers. The constitution prefers deletion to speculative abstraction in
framework code. Fix: delete, or demote to the storybook package until a real
surface needs them.

---

## Tier 4 — Polish (mostly single-engine, lower severity)

- **Recipe outliers / color props:** `Pager` and `Tree` hand-roll class constants
  instead of `tv`; `Tree` exposes `iconColor?: string` written as inline CSS →
  `pagerVariants`/`treeVariants` + token-backed `iconTone` (`ToneName`).
- **Inconsistent defaults:** `switch`/`toggle-group` default `size:"sm"` while the
  family defaults to `"md"`; icon-size spelling differs (`Button` iconSm/Md/Lg vs
  `Toggle` icon/icon-sm); `badge.shape:"rounded"` vs `chip.shape:"pill"`.
- **Repeated mechanisms:** the `variant context + useXVariant + Object.assign`
  pattern reimplemented in tabs/accordion/collapsible → `createVariantContext`;
  `SectionEyebrow` == `form-layout` kicker; labeled-control row dup in
  switch/radio/field.
- **Class merge:** `cn()` vs `.filter(Boolean).join(" ")` mixed in the list views.
- **Primitive export convention:** Select/Tooltip split convenience vs
  `*Primitive` namespaces (Codex flags inconsistency; Claude judged the surface
  otherwise strongly consistent — low-stakes, pick one).
- **Storybook:** dead `args`/`argTypes` ignored by `render: () =>` (~30 files);
  data-bound stories re-compose the provider stack the preview already supplies
  and hand-roll a GraphQL mock that `@angee/base/testing` already owns; inline
  `status→tone` maps; hard-coded hex/`white` in hero stories; inconsistent group
  taxonomy (`Feedback` group with one member; `Page` vs `Layouts` split).
- **Misc DRY:** inline name-entry control (×3 storage/knowledge); "Refreshing"
  badge as `Tag` vs `Badge`; `useOperatorSnapshot` `want*` flags spelled 4×;
  list/form declaration placement (hoisted vs inline) unprincipled in agents;
  dead import in `storage/.../file-rows.ts`; SDK dead `by` alias on
  `GroupByDimension`; two overlapping translators (`useT` vs
  `translateWithFallback`) stacked so fallback+interpolation runs twice.

---

## Suggested execution order

Sequenced so the shared owners land first, then the consumers collapse onto them.

1. **Design-system spine (T1, T2, T3, T5):** settle the `intent`/`tone`/`variant`
   vocabulary and negative-state word; make `tones` + `INTENT_GLYPHS` the single
   source; register the missing glyph names and add `Glyph.fallbackName`. Unblocks
   most UX consistency. *(Touches recipes broadly — do it as one deliberate pass.)*
2. **State surfaces (T4):** align `EmptyState`/`InlineEmpty`/`LoadingPanel`/
   `ErrorBanner` prop names; add `CollectionStatus`/`DataViewFrame`; delete the
   hand-rolled loading/empty forks.
3. **Find-the-owner pushes (T8) + SDK lifts (T10, T11):** move shape-sniffing onto
   SDK/model metadata, `Glyph`, the widget resolver, and `ChromeMenuNode`; lift the
   no-owner helpers; fix the aggregate-staleness bug.
4. **Correctness (T12, T18):** `runActionResult` for `ok:false`; add the Tailwind
   `@source` entries. Small, high-value.
5. **De-fork components (T9):** menu factory, overlay-surface constant, one
   list/data-view shell + activation primitive, one field stack, one tag primitive,
   one header, one shell-portal slot, one page-chrome, `DaemonResourceTable`,
   `ScopedTreeExplorer`, the date/chip widget primitives, one command palette.
6. **Architecture decisions (T6, T13, T15, T16, T17, T19):** i18n commit-or-drop;
   previews via `composeAddons`; `defineBaseAddon`; de-leak product specifics from
   chrome; forms onto the DSL/registry; delete the unused fragments.
7. **Tier 4 polish** alongside whatever slice each touches.

## Decisions that need the human architect

- **T6** — commit to i18n (base bundle + per-addon namespaces) or drop the
  half-measure?
- **T15** — add `defineBaseAddon()` as the rendered-addon declaration owner?
- **T1** — confirm the role-based vocabulary (`intent`/`tone`/`variant`) and the
  one negative-state word before the rename pass.
- **T19** — delete vs demote the speculative fragments.

## Cross-engine notes

- The two engines agreed on **every Tier-1/2/3 theme** except T15 (where Claude's
  deeper read corrected a Codex false-positive into a real, smaller finding) and
  the Tier-4 primitive-export nit. Treat ⊕⊕ items as high-confidence.
- Codex was stronger on **build/runtime** correctness (`@source`, `iconColor`
  inline style, business semantics in `tones`, product specifics in chrome,
  `UserMenu` faking `role="menu"`). Claude was stronger on **dead/unearned code**
  (the 12 unused fragments, the dead-but-shipped command palette) and on long DRY
  chains with exact `path:line` provenance.
