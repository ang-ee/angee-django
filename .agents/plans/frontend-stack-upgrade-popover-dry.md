# Frontend Plan — Base UI Upgrade · ListView Freeze Fix · P1 UI Lift Inventory

This file consolidates two things:

- **Part A (§0–§9 below):** the frontend stack upgrade and the ListView hard-freeze — both
  **done** (committed `424dfba` + `40f3311`). History worth keeping: the freeze was first
  theorized as a Base UI popover *event-ordering* bug, then as a nuqs/`useTransition` DRY
  cleanup. **Both theories were wrong.** The real cause was **TanStack Table
  `autoResetPageIndex`** looping on a churning `rows` reference, which hard-locks
  Safari/WebKit; fixed in `ListView` (`autoResetPageIndex:false`) + `useResourceList`
  (memoize `extractPage`). The Base UI rc.0 → 1.5 upgrade shipped too, but as an
  **independent** cleanup off a deprecated package, *not* the fix.
- **Part B (appended below):** the **P1 UI lift inventory** — a forward-looking checklist of
  the entire P1 notes UI surface to reconstruct in this repo. Folded in verbatim from the
  former `.agents/plans/p1-ui-inventory.md` (status there: inventory, not implementation).

**North star:** less code, defer to the stack, find the owner.

---

## ⏯ Resume state — read first if this session was compacted (2026-06-01)

**Branch** `wip-base-lift-refactor`. **Goal:** enhanced/cleaner/denser reconstruction —
mockup look&feel + P1 features — **storybook-first** (Stage 1 visual → Stage 2 wire). Full
checklist in §10; file-level surface in the P1 UI Inventory (bottom).

**Committed:**
- `424dfba` — Base UI `rc.0` → `@base-ui/react@1.5.0` (rename across 14 sites + 3 ref-type fixes + deleted the `PopoverTrigger` rc-era hack).
- `40f3311` — ListView hard-freeze fix: `autoResetPageIndex:false` in `ListView.tsx` + memoize `extractPage(run.data)` in `useResourceList`. Safari/WebKit-verified (the freeze was TanStack Table, NOT the popover — earlier popover/`useTransition` theories were wrong).
- `ea860c3` — storybook-first master checklist (§10) + this resume block.
- `4740a6a` — **slice #1 accepted:** data-driven chrome icon registry (Glyph→runtime registry single owner; trimmed vocab; AppRail fallback; menu/nav icons render). Full cadence ran: codex lift → review caught the dual-registry → codex rework → verified clean + visual. Stage-1.0 follow-up: base-icon resolution in Storybook (§10 1.0/1.1).

**Uncommitted working tree:** only this plan's latest edits. No code pending.

**References & live services:**
- **Look&feel target:** mockup `../angee-console-mockups/angee-console-react` → http://localhost:5174/#/notes (user-run; dense grouped rows, status badges, tag chips, avatars). Screenshot it to compare.
- **HARD RULE — NEVER synthesize/guess a visual.** Every asset, markup, class set, and layout already exists in the mockup (`../angee-console-mockups/angee-console-react/{src,public}`) and P1. Pull the EXACT source and copy it verbatim (architect override permits visual copy). Do not approximate with library params or a lookalike. E.g. the brand mark IS the mockup's `public/logo-icon.svg` (now inlined as `chrome/AngeeMark.tsx`) — not an `AngeeLogo` geometry guess. If the exact source isn't found yet, FIND IT before writing; don't invent.
- **Feature source:** `../angee-django-p1` (P1, read-only). P1 storybook on :6006 failed to start.
- **Our app:** `angee dev` on :5173 (I control it; it dies when the launching shell exits — restart with `angee dev` backgrounded, then poll 5173).
- **Verify/repro harness (gitignored):** `examples/notes-angee/e2e/test-results/{check,repro2,repro3,shot,mockup}.mjs` — Playwright (chromium + webkit installed); login alice/alice; the freeze repro = load `/notes?group=updatedAt:year` then open the filter popover, check in **WebKit**.

**Engine & review:** lift via the **Codex plugin** (`codex:codex-rescue` subagent; `codex` CLI ready + ChatGPT auth). Every slice gets THREE reviews: `architecture-reviewer` + `react-reviewer` + **visual-vs-mockup** (the missing lens that let a weak look through).

**Open decisions:** D-A (nuqs vs TanStack Router search) gates Stage 2; token/visual-layout copy is allowed (architect override), all other logic reconstructed. **Resolved:** addon UI → `src/<namespace>/<addon>/web/` (e.g. `src/angee/auth/web/`), co-located with addon backend; base UI stays in `packages/base`. Storybook host → `packages/storybook/` (default, unless changed).

**Autonomous loop active:** `/loop` cron `bc7f168e` fires every 15 min — each fire checks Codex progress; if idle, runs the next §10 slice via Codex + reviewers (anchored to exact mockup/P1 sources, screenshot-vs-mockup gated); when the whole lift is done, builds e2e for all logins (alice/bob/admin) exercising every function/button.

**Next action / in-flight:** Icon slices committed (`4740a6a`, `bd4c934` real Angee mark). **Slice 1.0 (Storybook) ACCEPTED + committed** (`f709f0d` scaffold, `e1867a4` review-fixes). Full cadence ran: Codex lift → both reviewers (found a self-inflicted toolchain fork: unused `@vitejs/plugin-react@6` forced vite 8, plus `react-router-dom`/TS 6 divergence; and a TopMenu story that threw — no `NuqsAdapter`) → fixed (dropped the unused deps, realigned vite ^6 / TS ^5.7, added `NuqsTestingAdapter` to the preview decorator, `@types/node` ^22, tsconfig scoped to TS sources) → verified: workspace typecheck green, Storybook (`:6006`) runs, Button + TopMenu stories **render with icons** (screenshot `test-results/sb-topmenu.png`). Lesson reinforced: verify stories by rendering, not just typecheck.
**Full workspace upgrade — COMMITTED + functionally verified.** Codex bumped everything (vite 8, TS 6, vitest 4, happy-dom 20, urql core 6 + graphcache 9, lucide 1.17, @types/node 25, plugin-react 6) but its verify hung (killed stuck pnpm) and left breakage I fixed: (1) `graphql-client.ts` `FetchBody` import → `@urql/core/internal` (urql 6 moved it); (2) added `@types/react`/`@types/react-dom` to storybook (TS 6 jsx-runtime). **Workspace typecheck GREEN.** Runtime VERIFIED: app `:5173` loads "Records 1-50 / 10052" under urql 6/9, lucide 1.17 icons render. **RESOLVED:** the 10 `@angee/sdk` failures were urql 6's new default `preferGetMethod: 'within-url-limit'` — small queries went via GET (no POST body), so the tests' `bodies[]` assertions saw nothing. Fix is one line in the real client (`createUrqlClient`): `preferGetMethod: false` (the Django endpoint is CSRF-protected and reads the operation from the POST body). **ALL GREEN:** sdk 113 ✓, base 26 ✓, workspace typecheck ✓, app loads data ✓. Upgrade committed as one milestone. Next: reviewers on the upgrade diff, then §10 1.1 page layout.

**Next (architect, reprioritized):** **Upgrade the WHOLE workspace to latest NOW** — don't pin packages back. The storybook vite^6/TS^5.7 pins (`e1867a4`) are temporary; the real fix is leveling the workspace UP (vite→8, @vitejs/plugin-react→6, vitest→4, happy-dom→20, typescript→6, @types/node→25, @urql/core→6, @urql/exchange-graphcache→9, lucide-react→1.17, @graphql-codegen/cli→latest), then storybook needs no pins. This promotes the former "Carryover" phase to the front. Verify: workspace typecheck + base/sdk tests + app (`:5173`) + storybook (`:6006`) all green. THEN advance **§10 1.1 `page/` layout** (the empty-band/weak-header/density fix), screenshot-vs-mockup gated.

---

## ⚠ Open visual issues — consolidated (user-flagged "still many", 2026-06-02)

The single tracked list of unresolved look&feel/behavior gaps, so none scatter.
Each is driven by the standard cadence (Codex → arch + react + visual-vs-mockup
review → render-verify on live `:5173` vs mockup `:5174`).

- [x] **V1 — grey band above content (every console page). DONE (commit
  8853b5d).** Root cause was `console-grid` reserving a fixed-height `control`
  row that `ConsoleShell` never filled. Fix: control row → `auto` height + an
  unstyled `area-control` host; a `ControlBand` context/portal primitive carries
  the flush `bg-sheet` band styling; the list `DataToolbar` moved into it (flush
  under the breadcrumb), the body renders standalone. Reviewed (arch+react, no
  blockers; folded token/barrel/doc/test), render-verified live (band gone, 0
  GraphQL errors, 3 new portal tests). Form/record toolbar still in the card →
  folds into V4.
- [x] **V2 — aggregate chain (grouped list). DONE (full mockup parity, 2 slices).**
  Was: only a bare count chip per group. Now each group header shows count + the
  measured column's rollup (e.g. "411 words") and a footer grand total ("Total
  414,808 words"). **V2a (backend, 667cb39):** `word_count` denormalized to a
  real column (computed on save via a shared `count_words` owner, backfilled,
  seed-populated), exposed + added to the order and `aggregate_fields`, Word
  Count sort re-enabled. **V2b (SDK+frontend):** `AggregateBucket` carries
  per-bucket measures; `assembleGroupByDocument`/`assembleAggregateDocument` +
  `useResourceGroupBy`/`useResourceAggregate` select requested measures (stable
  vars); the grouped list derives measures from the `ColumnDescriptor.aggregate`
  marker and renders per-group values + a footer grand total (whole-list filter,
  not page-derived); the flat-list `GroupHeader` was generalized off its
  hardcoded `wordCount`. Reviewed (django/react/arch, no blockers, findings
  folded); live-verified, 0 errors.
- [x] **V3 — FormView fields blanked after save until reload. DONE.** Original
  "SDK cache" hypothesis was WRONG — live tracing proved the mutation persists,
  the result returns the full node, and the cache normalizes (the breadcrumb/
  other views update with no reload). The real cause: `@tanstack/react-form`
  1.33.0 re-seeds an *untouched* form from `defaultValues` whenever that ref
  deep-changes, and the post-save `onSaved → onSelect` re-render passed a fresh
  empty-draft `defaultValues`, clobbering the just-saved values after
  `form.reset(savedValues)`. Fix: feed `useForm` from a stable `baselineValuesRef`
  (the single baseline authority, reassigned only on seed/save/create-reset) +
  guard comment. Browser-free regression test added (fails pre-fix). Reviewed
  (react+arch, no blockers); live-verified the title persists post-save, 0 errors.
- [x] **V4 — FormView layout (redefined by user). DONE.** User: "we do not need
  tabs but i want the layout of the form of p1 — with the save discard dirty
  buttons in the view tool bar." Removed the notebook/Tabs (body renders inline
  as a full-width section); the P1 sheet layout stays (title row + status stepper
  + 2-col details grid + body); the dirty-only Save/Discard moved OUT of the form
  into the shell control band (the view toolbar) via a `ControlBand` portal, with
  a brand-soft highlight when dirty and Save submitting through `handleSubmit()`
  (it's portaled outside the `<form>`). FormView gained a `toolbar` prop;
  DataPage feeds the record pager + view switcher into it and dropped its
  in-content record toolbar. Removed all notebook machinery + the
  `FORM_VIEW_NOTEBOOK_SLOT` export. (Deferred, not requested: smart-buttons /
  More-actions menu — need record-relation data.) Reviewed (react+arch, no
  blockers); live-verified (no tabs, body inline, Save/Discard in toolbar, save
  persists, 0 errors).
- [x] **V5 — FormView record pager scope (total-only + page-local). DONE.** The
  pager used to vanish over a grouped list (the flat one-page probe couldn't
  locate a record opened from an old date-group). Now `buildRecordNavigation`
  always returns a nav when a record is open: it shows the filtered total
  ("of N"), with page-local Prev/Next when the record is in the loaded slice and
  disabled otherwise (no position query, per the chosen scope). Lives in the
  control band alongside Save/Discard (V4). Verified live.
- [ ] **V6 — general fidelity vs mockup.** Density, real page header, focus/border
  states (umbrella; V1 is its headline instance).
- [x] **V7 — right-edge bleed. DONE (root flagged).** The clipped purple
  underline was the **Chatter** tablist (Angee/Comments/Activity): its 3 tabs are
  ~269px but the panel gives ~253px with `overflow-x: visible`, so the last tab's
  active underline bled past the panel edge. Fixed by tightening the tabs
  (`px-2`/`font-medium`, matching the reference) + `overflow-x-auto min-w-0` on
  the tablist so it clips cleanly + scrolls instead of bleeding. **Deeper root
  flagged (separate follow-up):** the chatter renders at its MIN width (260px),
  not the 332px default — its resize `Panel.onResize → updateWidth` feeds the
  panel size (aside − handle) back into the aside `width`, collapsing it to min
  on mount. Fixing that (so the chatter is 332 and all 3 tabs fit without scroll)
  touches the resize wiring and is out of this minor triage's scope.
- [x] **V8 — group order ignored the column sort. DONE.** A grouped list's
  column sort only ordered within-group rows; the buckets ignored it. Wired an
  optional group `orderBy` through `assembleGroupByDocument` + `useResourceGroupBy`
  and translate `dataView.state.sort` → per-level bucket order in the grouped
  list. Backend already exposed it; accepted field is the snake_case bucketed
  alias (`updated_at_day`). Reviewed (folded contract-drift fix +`expectValid`,
  `fieldToSnake` dedupe, translator relocated, tighter types); verified live
  (asc→oldest, desc→newest, 0 errors) + sdk/base suites green.
- [x] **V9 — sort offered on non-orderable columns (GraphQL crash). DONE (commit
  8853b5d).** Headers showed sort toggles on every column, but `NoteOrder @oneOf`
  only accepts title/status/updatedAt/createdAt; sorting Word Count/Tags raised
  "Field not defined by type / @oneOf must specify exactly one key". `word_count`
  is a Python property and `tags` is M2M — neither is DB-orderable, so the notes
  columns now mark them `sortable: false` (the header affordance already gated on
  `ColumnDescriptor.sortable`). Verified live (0 errors).

---

> **Reconcile with the single source of truth.** `.agents/plans/notes-auth-lift.md` is
> "the one plan." This file is a focused sub-plan for one workstream; it must not
> contradict it. In particular it is **gated by that plan's open decision D-A — view-state
> store: `nuqs` vs TanStack Router search.** The `useTransition` fix below lands regardless
> of D-A; the nuqs-specific option wiring only applies if D-A keeps nuqs.

---

## 0. Status ledger

- ✅ **Stage 0 — frontend inventory** (research done; table in §4).
- ✅ **REAL freeze root cause found (NOT Base UI).** The popover/Base-UI theory was
  **wrong**. The freeze is **TanStack Table `autoResetPageIndex`**: ListView passes
  `data: rows` to `useReactTable`, and `rows` (from urql) churns identity across renders, so
  the table auto-resets its page index → `setPagination` → `onStateChange` → setState →
  re-render → new `data` identity → reset again = infinite loop. It hard-locks **WebKit/
  Safari** (deterministic; racy/often-survivable in Chromium). Found via CPU profile call
  tree: top-level trigger task `table.resetPageIndex → setPagination → setState →
  onStateChange`. Needs all of: list view (TanStack Virtual present) + an active group +
  opening the filter popover — the grouped re-render storm keeps the reset loop fed.
- ✅ **FIX (one change):** `autoResetPageIndex: false` + `autoResetExpanded: false` on
  `useReactTable` in `ListView.tsx`. Pagination/sort/filter/group are owned by the
  data-view/URL state, never the table, so auto-reset must be off. Verified in **WebKit**
  (`?group=updatedAt:year|day|year&then=status`, all RESPONSIVE, stable ×3); typecheck +
  26 unit tests green. Repros: `examples/notes-angee/e2e/test-results/{check,repro*}.mjs`.
- ✅ **Follow-up stabilization (owner-level):** `useResourceList` re-derived `rows` via
  `extractPage(run.data)` every render (fresh array even on a urql cache hit) — the churn
  that fed the reset. Memoized on `run.data` identity in `packages/sdk/src/resource-hooks.ts`,
  so every consumer gets a stable `rows`. Workspace typecheck + 113 SDK / 26 base tests green;
  WebKit re-verified RESPONSIVE. Base UI upgrade (Stage 1) **kept** per architect.
- ⚠️ **Earlier false-positive:** my first Playwright pass tested *incrementally adding* a
  group (popover already open) — which dodges the loop — and wrongly concluded Base UI fixed
  it. The deterministic repro is **loading `/notes` with group state in the URL, then
  opening the popover**, and it must be checked in **WebKit**, not Chromium.
- ✅ **Stage 1 — Base UI rename + upgrade** done (`@base-ui-components/react@1.0.0-rc.0` →
  `@base-ui/react@1.5.0`, 14 import sites, 3 ref-type drifts, `PopoverTrigger` rc.0 hack
  deleted, `docs/stack.md` updated; typecheck + 26 tests green). **Independent of the freeze**
  — a legitimate cleanup (off a deprecated dep), but it did **not** fix the freeze.
- ⛔ **Stage 2 — data-view `useTransition`** — **not a free `setTimeout`→`useTransition`
  swap; reverted.** nuqs already owns the deferral (above). Removing the `setTimeout(0)`
  reintroduces the **stale-closure batch drop**: the dispatch builds a *full* query-state
  snapshot via `dataViewReducer`, so two dispatches batched in one commit (the pageSize-sync
  and defaultGroup-seeding effects fire together) each read the same stale `current` and
  clobber each other — the `setTimeout(0)` was serializing them. Belongs to **D-A** (store
  redesign to partial patches), not the freeze fix.
- ⬜ **Stage 3 — build/test toolchain** (vite · @vitejs/plugin-react · vitest · happy-dom · TS).
- ⬜ **Stage 4 — urql data layer** (`@urql/core` + `@urql/exchange-graphcache`).
- ⬜ **Stage 5 — lucide-react** (icon-set major; audit the name-referenced registry).
- ⬜ **Stage 6 — dev types/tooling** (`@types/node`, `@graphql-codegen/cli` patch).
- ⬜ **Backend stack** — separate `uv` inventory pass; **not yet run** (see §8).

---

## 1. Root cause (why this refactor exists)

When the filter popover is open and you click a toolbar chip that lives in the
`PopoverRoot` subtree but **outside** the portaled popup (the active-filter `FacetChip`
"Remove Draft", `DataToolbar.tsx:207-215`), one trusted native click drives three things
in the same tick:

1. **Base UI dismissal** — outside-press teardown runs **synchronously** (portal unmount,
   positioner cleanup, focus return). `PopoverRoot` is exported raw (`popover.tsx:75`).
2. **The chip's `onClick`** → `onFilterToggle` (`DataToolbar.tsx:300-307`) →
   `dataView.setFilter` (`ListView.tsx:320-324`).
3. That cascades to a query/URL write + a urql reload + virtualizer recompute.

The synchronous teardown reenters mid-commit alongside the heavy reload → renderer lock.
It only shows up on this branch because view-state went **URL/query-backed**, making
filter/group/page clicks heavy and synchronous.

**The DRY smell — three hand-rolled defers, none delegating to the owner:**

- `PopoverTrigger` (`popover.tsx:83-149`): ~65 lines of capture-phase event archaeology —
  swallow the trusted press, `setTimeout(0)`-re-dispatch `trigger.click()`. Reinvents Base
  UI's controlled `open`/`onOpenChange`. Smells like an rc.0 trigger-press bug workaround.
- `data-view-context.tsx:89-125`: every dispatch wrapped in `setTimeout(0)` + a
  `scheduledDispatchesRef` Set + `mountedRef` cleanup. Reinvents `useTransition` +
  nuqs's `startTransition`/`limitUrlUpdates`. `useQueryStates(..., { history })` passes
  **none** of nuqs's perf options.
- **The outside-press path has no defer at all** — the hole the chip falls through.

## 2. Prior art / owners (defer to the stack)

| Concern | Owner in our stack | What we do |
| --- | --- | --- |
| "Heavy update must not block/tear urgent UI work" | **React 19.2 `useTransition`/`startTransition`** | Mark the view-state→reload update as a transition so React commits the urgent popover teardown first and renders the reload interruptibly. |
| URL/query write timing | **nuqs 2.8.9** (`startTransition`, `limitUrlUpdates: throttle/debounce`, `shallow`) | Pass the transition's `startTransition` + a throttle to `useQueryStates`; nuqs already updates state instantly and rate-limits only the history write. **Contingent on D-A.** |
| Popover dismissal timing | **Base UI** controlled `open`/`onOpenChange(open, eventDetails)` — `reason` (`outside-press`/`trigger-press`/…), native `event`, `cancel()`, `actionsRef` | Upgrade off rc.0; keep `PopoverRoot`/`PopoverTrigger` thin re-exports; delete the bespoke trigger hack. |

Sources: Base UI Popover docs + releases (now **1.2.0**, package renamed
`@base-ui-components/react` → `@base-ui/react`); base-ui #1744 (`onOpenChange` event-detail
change); nuqs options docs ("state is always updated instantly … only URL changes are
throttled/debounced").

## 3. Locked constraints (apply to every stage)

- **Each changed/added dep carries a `docs/stack.md` owner-row update in the same change**
  (repo rule). The Base UI rename row is mandatory in Stage 1.
- **Remove dead code before the structural refactor** — delete the hacks, don't wrap them.
- **Re-read a file before editing and after; verify before claiming done.** Drift is a bug.
- **One stage = one commit** for clean bisect/revert. **Checkpoint the dirty tree first**
  (36 files dirty on `wip-base-lift-refactor`, incl. `@angee/sdk` — upgrading deps on top
  of uncommitted work hurts reversibility).
- **Run from the root.** Stack via `angee dev`; unit via `vitest run`; e2e via Playwright
  (`docs/testing/e2e.md`).
- **Do not contradict `notes-auth-lift.md`.** Resolve **D-A** before wiring nuqs options.

---

## 4. Stage 0 — Inventory (`pnpm -r outdated`, June 2026)

| Package | Current | Latest | Bump | Stage |
| --- | --- | --- | --- | --- |
| `@base-ui-components/react` | 1.0.0-rc.0 | **Deprecated** → `@base-ui/react` ≥1.2 | rename + pre-1.0→1.2 | **1** |
| `@urql/core` | 5.2.0 | 6.0.1 | major | 4 |
| `@urql/exchange-graphcache` | 7.2.4 | 9.0.0 | 2 majors | 4 |
| `vite` (dev) | 6.4.2 | 8.0.14 | 2 majors | 3 |
| `@vitejs/plugin-react` (dev) | 4.7.0 | 6.0.2 | 2 majors | 3 |
| `vitest` (dev) | 3.2.4 | 4.1.7 | major | 3 |
| `happy-dom` (dev) | 16.8.1 | 20.9.0 | 4 majors | 3 |
| `typescript` (dev) | 5.9.3 | 6.0.3 | major | 3 |
| `lucide-react` | 0.469.0 | 1.17.0 | major (icon set) | 5 |
| `@types/node` (dev) | 22.19.19 | 25.9.1 | major (types only) | 6 |
| `@graphql-codegen/cli` (dev) | 7.1.0 | 7.1.1 | patch | 6 |

React (`19.2.6`) and nuqs (`2.8.9`) are **current** — the concurrency + URL-state owners we
rely on are already in place; no bump needed for the bug fix.

---

## 5. Staged upgrade plan (dependency-ordered)

### Stage 1 — Base UI rename + upgrade ✅ *(done; freeze fix pending e2e)*
- Renamed `@base-ui-components/react@1.0.0-rc.0` → `@base-ui/react@^1.5.0` (latest 1.5.0)
  in `packages/base/package.json`; rewrote **14 import sites** under `packages/base/src/ui/*`
  + `packages/base/src/lib/slot.ts` (subpaths unchanged).
- Fixed 3 ref-type drifts (1.5 narrowed part ref element types): `SelectContent`→
  `HTMLDivElement`, `TabsTab`→`HTMLElement`, `ToolbarGroup`→`HTMLDivElement`. v1.0 release
  notes confirm the rename is the only breaking change; the rest is non-breaking.
- **Deleted the `PopoverTrigger` rc.0 hack** (`shouldDeferPopoverPress`, `stopPopoverPress`,
  `pendingPointerPressRef`, the `setTimeout` re-dispatch) → `export const PopoverTrigger =
  BasePopover.Trigger`.
- Updated the `docs/stack.md` row to `@base-ui/react`.
- ✅ Workspace typecheck clean; 26 `@angee/base` unit tests green.
- ⬜ **Remaining gate:** e2e/live — confirm (a) the outside-press freeze is gone and (b)
  trigger open/close works by pointer **and** keyboard. Unit tests can't reproduce
  floating-ui teardown.

### Stage 2 — data-view dispatch ⛔ *(reverted; folded into D-A)*
**Finding:** nuqs's react-router adapter already wraps every URL-state update in React
`startTransition` *and* an internal throttle queue, so the heavy reload is already deferred
and de-prioritized — there is nothing for us to add. The `setTimeout(0)` in
`data-view-context.tsx` is **not** a stray defer to delete: it serializes dispatches so the
*full-state-snapshot* updater (`dataViewReducer` over the whole state) doesn't clobber a
sibling dispatch batched in the same commit (pageSize-sync + defaultGroup-seeding effects).
Removing it (whether direct or `useTransition`) reintroduces the **stale-closure batch
drop** — three `DataPage.test.tsx` cases fail (`Remove group`, `Records 1-2 / 4`, `Month`).
- **Proper fix lives in D-A:** redesign the dispatch to emit **partial** query patches
  (per-key), not full snapshots — then batching is safe and the `setTimeout`/`mountedRef`
  bookkeeping drops out for free. Do it with the store decision, not the freeze fix.
- Keep `PopoverRoot`/`PopoverContent` as thin styled re-exports; **no bespoke defer wrapper**
  anywhere (nuqs + Base UI own the timing).

### Stage 3 — build/test toolchain (coupled)
- Bump together (they constrain each other): `vite 6→8`, `@vitejs/plugin-react 4→6`,
  `vitest 3→4`, `happy-dom 16→20`, `typescript 5.9→6`.
- Expect: Vitest 4 config/API shifts; TS 6 surfacing new strictness; happy-dom DOM-API
  deltas affecting `@vitest-environment happy-dom` tests.
- Gate: full `vitest run` across `@angee/base` + `@angee/sdk` green; `tsc` clean; example
  web build (`vite build`) succeeds.

### Stage 4 — urql data layer (coupled, higher risk)
- `@urql/core 5→6` + `@urql/exchange-graphcache 7→9` together. Touches `@angee/sdk`
  (`resource-hooks`, `document-query`, `aggregates`, invalidation wiring) — note these are
  **already dirty** in the working tree; reconcile, don't clobber.
- `docs/stack.md`: confirm the urql rows still describe the wiring accurately.
- Gate: `@angee/sdk` tests green; live reads/subscriptions verified via `angee dev` + e2e.

### Stage 5 — lucide-react `0.469 → 1.17` (icon set)
- Major icon-set bump; names can change/deprecate. Audit the **name-referenced icon
  registry** (`docs/stack.md` lucide row; `Glyph`/icon usage in chrome + toolbars).
- Gate: no missing-icon fallbacks; visual smoke of nav/toolbar/menus.

### Stage 6 — dev types/tooling (low risk)
- `@types/node 22→25`, `@graphql-codegen/cli 7.1.0→7.1.1`.
- Gate: typecheck + codegen (if used) clean.

---

## 6. Verification — the freeze regression

- **Unit (`DataPage.test.tsx` conventions):** open the filter popover, activate a filter,
  then with the popover open click the outside `Remove <label>` chip; assert the filter is
  removed and the popover closes (flush with `nextTask()`, `DataPage.test.tsx:371`). This
  guards behavior but **cannot** reproduce the lock (no real floating-ui in happy-dom).
- **E2E (Playwright, the real gate):** the lock only reproduces with live floating-ui +
  router. Script: open filter popover → click outside "Remove Draft" → assert page stays
  responsive and filter/URL updated. Per `docs/testing/e2e.md`. **This is the gate that
  confirms the Stage 1 upgrade actually fixed the freeze.**

## 7. Sequencing & gates

1. Checkpoint the dirty tree (done — committed) so each upgrade is revertible.
2. **Stage 1 ✅ (the freeze fix)** → run the §6 e2e repro to confirm the lock is gone.
3. Stages 3 → 4 → 5 → 6 (toolchain → urql → icons → dev types), each its own commit,
   each gate green before the next.
4. **D-A** (architect) → the data-view dispatch redesign (former Stage 2): partial
   query patches, which also retires the `setTimeout`/`mountedRef` bookkeeping.
5. Per-stage gate: `tsc` clean · `vitest run` green · example `vite build` ok ·
   relevant e2e subset green against `angee dev` · `docs/stack.md` row updated for any
   dep whose name/ownership changed.

## 8. Open decisions / not-yet-scoped

- **D-A (from `notes-auth-lift.md`):** nuqs vs TanStack Router search as the view-state
  owner. **Now owns the data-view dispatch redesign** (former Stage 2). The `setTimeout(0)`
  is load-bearing because the dispatch emits *full-state snapshots*: confirmed by reverting
  it and watching `Remove group` / `Records 1-2 / 4` / `Month` drop (the M3 P1-review
  "stale-closure batch drop"). Fix = per-key **partial** patches (then the timer bookkeeping
  drops out), decided alongside the store. nuqs already provides `startTransition` + throttle
  internally, so no explicit transition is needed either way.
- **TS 6 / Vite 8 / Vitest 4 compatibility:** verify peer ranges at run time; may need
  lockstep or a held-back pin if an ecosystem plugin lags.
- **Backend stack ("entire stack"):** Python deps (`pyproject.toml` + `uv.lock`) are **not
  inventoried here.** Needs its own pass (`uv tree --outdated` / `uv lock --upgrade` dry
  run) and its own plan section before touching — Django/Strawberry/zed-rebac majors carry
  migration + SDL-emit risk. Flag to architect: in scope now, or separate?

## 9. Pointers (kept, not duplicated)

- Bug owner: `packages/base/src/ui/popover.tsx` (`PopoverTrigger` now a thin re-export).
- Deferral owner: **nuqs** (react-router adapter: `startTransition` + throttle queue) — not
  our code. `data-view-context.tsx` `setTimeout(0)` only serializes full-snapshot dispatches.
- Toolbar chips (the outside-press source): `packages/base/src/toolbars/DataToolbar.tsx`.
- Adapter wiring: `packages/base/src/createApp.tsx:213` (`NuqsAdapter`).
- Single-source plan + D-A: `.agents/plans/notes-auth-lift.md` (§2).
- Stack ownership table: `docs/stack.md`.

---

## 10. Execution TODO — UX & P1 design parity (tracked)

Each item carries three boxes:
- **done** — implemented; `tsc` + lint green.
- **verified** — automated check (unit/e2e) **and** a WebKit/live check per the area's
  guideline (the Safari freeze proved Chromium-only checks aren't enough).
- **checked by human (final qa)** — Alexis signs off on the actual UX/visual result.

> **Token / visual-layer copy policy — architect override (2026-06-01).** The repo default
> is *reconstruct, never copy, strip provenance* (`notes-auth-lift.md` §1). For the
> **design tokens and the visual/layout layer only**, copying from P1 verbatim is
> **approved** — there's no value in re-deriving a palette/scale, and parity is the point.
> The binding constraint is **clean decomposition: no monolithic/spaghetti files** — split
> tokens and layout primitives into small, single-purpose pieces that fit this repo's
> structure. Scope is tokens + visual layout; component *logic* still follows
> find-the-owner/DRY. Reconcile this override into `notes-auth-lift.md`. P1 source (read-only):
> `../angee-django-p1`. Detailed file-level surface is in the **P1 UI Inventory** below.

> **Reference model & goal (architect, 2026-06-01).** The goal is an **enhanced, cleaner,
> denser reconstruction — NOT a weak copy.** Two distinct references:
> - **Look & feel → the initial mockup** `../angee-console-mockups/angee-console-react`
>   (runs at `http://localhost:5174/#/notes`). This is the visual target: dense grouped
>   rows with counts, status badges, tag chips, assignee avatars, relative dates, real
>   toolbar + header. Screenshot it for any screen and match-or-beat it.
> - **Features → dissect P1** `../angee-django-p1` (functional behaviour/contracts).
>
> **Every UI slice gets THREE reviews, not two:** `architecture-reviewer` (boundaries/DRY/
> decomposition) + `react-reviewer` (hooks/render/TS) + a **VISUAL review** — screenshot
> ours vs the mockup at the same screen and judge density/polish/layout fidelity (code
> reviewers cannot see pixels; this lens was missing and let a weak look through).
>
> **Root of the weak copy (diagnosed):** P1/the mockup use a real page-layout system —
> `<Page>`→`<PageHeader>`→`PageControlBand`→`<PageBody>`(navigator aside + list). We lifted
> **none** of `page/` (9 files, absent), so our screen is a bare list with a stray empty
> band and no real header/density. The `page/` layout system is the first foundation lift.

### Approach — storybook-first (revised 2026-06-01, architect)
1. Stand up a clean **Storybook** in this repo. 2. Lift **every visual component** into it,
matched to the mockup look&feel — **presentational, no backend**. 3. **Then wire** components
to data/features per P1. Engine: **Codex lifts** each slice; **3 reviews per slice**
(architecture + react + visual-vs-mockup); each item carries done · verified · human QA.
File-level surface = the **P1 UI Inventory** below. Sift the source per slice; reconstruct,
don't copy (tokens/visual-layout exempt per override). Goal: enhanced, cleaner, denser.

### Fidelity method — capture placement from the live mockup, don't hand-list it
Don't enumerate fine-grained look&feel/placement details by hand (they get missed — e.g.
"Save/Discard go on **top**, not under the form"). Lift them mechanically from the two
ground truths: **P1 source** owns behaviour/contract; the **live mockup `:5174`** owns
visual + placement. Per surface:
1. **Capture mockup truth:** `node .agents/tools/fidelity-capture.mjs http://127.0.0.1:5174 <route> <name> --hash [--dirty]` → a screenshot + a **placement-aware control inventory** (`test-results/fidelity/<name>.json`): every visible control/heading with its layout **band** (top control strip / body / bottom) and **side** (left/right). `--dirty` types into the form to surface dirty-only controls. Mockup routes are react-router paths (`/notes`, `/notes/:id`, `/notes/:id/edit`, `/files/:id`, `/health/:id`, `/calendar`, `/settings/preferences`, …).
2. **Derive the fidelity spec** from the fingerprint + P1 source (slot order, conditional rules) — concrete, not prose.
3. **Diff our render:** run the same extractor on our Storybook story / app screen and compare the band/side/order fingerprint → placement, ordering, and missing-control drift surface automatically. This is the visual-vs-mockup gate; "weak copy" can't pass silently.
   - **Proven (form):** `/notes/1/edit --dirty` → `Discard` & `Save` both `band=top side=left` (dirty-only). Captured as the gate below.
   - **Known limitation:** the band/side fingerprint does NOT distinguish the rail from the top bar (both bucket to `top:left`) and counts a label once even if duplicated — so it MISSED a duplicate brand mark (Angee cube in the rail AND `<AppBrand>` wordmark in the top bar; fixed by dropping the top-bar brand in `ConsoleShell`/`TopBar` — the rail's `AppChooser` is the sole switcher/brand, per the mockup). TODO: extend the extractor to tag the rail vs top-bar landmark and flag duplicate brand/control labels. Until then, pair the fingerprint with an eyeball pass for the shell.

## STAGE 1 — All visual components in Storybook (no backend)

### 1.0 Storybook & visual tooling
- [x] done [x] verified [ ] checked by human (final qa) — Stand up a clean Storybook — `packages/storybook/` (Storybook 10 + react-vite, vite 6 / TS 5.7 aligned to workspace); loads tokens via `@angee/base/styles.css`; runs at `:6006`
- [x] done [x] verified [ ] checked by human (final qa) — Visual-review harness: **`.agents/tools/fidelity-capture.mjs`** — captures a screenshot + placement-aware control inventory (band/side) from the live mockup `:5174` (and from our stories), so look&feel/placement is lifted + diffed mechanically, not hand-listed (see "Fidelity method" above)
- [x] done [ ] verified [ ] checked by human (final qa) — Story conventions (CSF) established (Button + TopMenu); kitchen-sink overview story TBD
- [x] done [x] verified [ ] checked by human (final qa) — Base icons resolve in Storybook via a preview decorator seeding `baseIcons` into `AppRuntimeProvider` (TopMenu story renders icons)

### 1.1 Foundation — tokens / layout / primitives / fragments
- [x] done [x] verified [ ] checked by human (final qa) — Design tokens at P1 parity (185 vars identical; ours cleaner) — no copy needed
- [x] done [x] verified [ ] checked by human (final qa) — `page/` layout system (Page/PageHeader/PageToolbar/PageBody/PageAside/PageFooter/SplitPanes/SectionNav) + 10 stories ← fixes empty-band/weak-header. **Full cadence ran clean:** Codex lift → arch+react reviewers → Codex hung on a Storybook browser smoke-check (killed, restarted typecheck-only) → all fixes applied (SectionNav reuses `tabsVariants`; PageToolbar `role=toolbar` + ref cast gone; PageHeader `headingLevel`; SplitPaneHandle inherits group direction via context; PageBody `as` override; aria-current correctness; story units/tokens; `page/` shell naming note) → independently verified: base+storybook typecheck green, `SplitPanes`→react-resizable-panels ✓, zero provenance ✓. **Visual gate caught a broken `ConsoleShell` story** (needs router context the SB decorator lacks) → replaced with a content-region story that renders + documents scroll ownership; full ConsoleShell+Page story deferred to §10 1.2 (needs a router-in-Storybook decorator). All 10 stories render clean (`test-results/page-slice/`). Frame story = dense header + populated control band + dense table + inspector aside + footer pager → matches mockup. Committing.
- [x] done [x] verified [ ] checked by human (final qa) — `layouts/` (RecordView/SplitView/CanvasPage/HeroPage + slots) + `ui/form-layout` (FormGrid/FieldRow/FormActions/FormFooter/FormSectionKicker, grid-area placement) + 6 stories. **Codex runtime crashed 3× mid-task** (Storybook smoke-check, then process death) → finished directly: fixed the one typecheck error (`gridAreaStyle` `Array.isArray`→`typeof` narrowing for readonly[]) and wrote the 5 missing stories. Layouts are compound slot components composing the `page/` primitives (SplitView→SplitPanes). Verified: base+storybook typecheck green, all 6 stories render (`test-results/page-slice/10-15`), RecordView shows the form-mode record pager "Record 4 / 245". Lesson reinforced ([[codex-hung-job-restart]]): the flaky shared Codex runtime crashes repeatedly — finish small well-specified remainders directly rather than re-cycling.
- [x] done [x] verified [ ] checked by human (final qa) — **`ui/` primitives parity — COMPLETE** (4 batches, ~28 primitives, batched to limit Codex-runtime crash blast radius):
  - [x] done [x] verified [ ] checked by human (final qa) — **display batch** (avatar/kinded-avatar, status-icon, kbd, code, alert/banner, text-link, nav-link, section-eyebrow) + 28 stories. Codex clean this run; verified base+storybook typecheck green, all 8 exported, zero provenance, all stories render. Needed lucide icons registered in `chrome/icon-registry.ts`.
  - [x] done [x] verified [ ] checked by human (final qa) — **disclosure batch**: accordion, collapsible, drawer/sheet, toggle, toggle-group/segmented + 9 stories. Codex clean; deferred open/close/focus/keyboard to `@base-ui/react` (Accordion/Collapsible/Toggle/ToggleGroup; drawer on Dialog). Verified typecheck green, exported, zero provenance, all render.
  - [x] done [x] verified [ ] checked by human (final qa) — **overlay/menu batch**: dropdown-menu, context-menu, navigation-menu, command, selection-bar + 6 stories. Codex clean; deferred to Base UI Menu/ContextMenu/NavigationMenu + `cmdk` (command, matching `GlobalSearch.tsx`). Verified typecheck green, exported, zero provenance, all render — AND interaction-tested the dropdown (opens with groups, ⌘ shortcuts, checkbox/radio items, Export submenu, danger Archive). Note: overlay behavior needs interaction to verify, not just static render.
  - [x] done [x] verified [ ] checked by human (final qa) — **input batch**: slider, number-field, calendar, radio-group, form + 16 stories. Codex clean; deferred to Base UI Slider/NumberField/Radio/Form + `react-day-picker` (calendar); `form` composes existing `ui/field.tsx`. Verified typecheck green, exported, zero provenance, all render (calendar styled correctly).
- ⏳ **`fragments/` (30 in P1) — IN PROGRESS, batched** (composites built FROM the primitives/page/layouts):
  - [x] done [x] verified [ ] checked by human (final qa) — **batch 1 (core data-view/header/meta/state, 14)**: RecordHeader, CollectionHeader, SurfaceHeader, SectionTabs, InfoRow, MetaGrid, MetadataPanel, MetricStrip, MetricGrid, MiniCard, EmptyState, InlineEmpty, ErrorBanner, DirtyPill + 14 stories. Codex clean; each composes local primitives (RecordHeader←PageHeader+Badge, ErrorBanner←Alert, MetricStrip←Card…). Verified typecheck green, exported, zero provenance, all render (MetricStrip polished).
  - [x] done [x] verified [ ] checked by human (final qa) — **batch 2 (panels/composites, 12)**: SurfacePanel, FocusPanel, RailPanel, ListPanel(+ListItem), HeroPanel, MarketingHero(+BrandLockup/AnnouncementChip), LoadingPanel, EditorHeaderBar, FrameToolbar, DataLens, DialogForm, BrandButton + 18 stories. Codex clean; composed from Card/Collapsible/Toolbar/Dialog/Form + batch-1 fragments. Verified typecheck green, exported, zero provenance, all render. **Stage-2 note:** `DataLens` is presentational only — P1's `useQueryRows`/`QueryStateSchema` aren't on this branch, so the lens-switcher UI is built but data wiring is a stub to revisit when wiring §10 1.3 data views.
  - ⏸️ **DEFERRED (needs a deps-install pass)** — [ ] done [ ] verified [ ] checked by human (final qa) — **batch 3 (specialized)**: JsonViewer (react-json-view-lite), LogPanel (ansi-to-react), GraphCanvas + GraphIntrospection (@xyflow/react) + stories. These 3 libs have owner rows in `docs/stack.md` but are NOT yet in `packages/base/package.json`; installing them reshuffles node_modules and 500s the running dev servers, so batch them as a single `pnpm add` + server-restart pass (lower priority — debug/graph panels, not core data views).
- [x] done [x] verified [ ] checked by human (final qa) — Icon system: `Glyph`→runtime addon-icon registry (single owner), vocabulary trimmed, AppRail fallback, menu/nav icons render — committed `4740a6a`. **Stage-1.0 follow-up:** base icons resolve only via createApp seeding → add a static `baseIcons` fallback in `useIcon` (or a base-runtime Storybook decorator) so they render standalone.

### 1.2 Chrome / shell (presentational)
- [x] done [x] verified [ ] checked by human (final qa) — AppRail/AppBrand/TopBar/TopMenu/Breadcrumb/Systray/UserMenu/GlobalSearch/Spotlight/AppChooser + menu-tree + stories. Built the 3 missing (AppBrand, Spotlight via `ui/command`/cmdk, AppChooser); added a **TanStack Router decorator** to the Storybook preview (`@tanstack/react-router` dep) so chrome/shell stories with `Link`s render. Verified typecheck green, all stories render.
- [x] done [x] verified [ ] checked by human (final qa) — Shell/ConsoleShell/PublicShell layouts + stories. **ConsoleShell renders in Storybook now** (router decorator fixed the prior `isServer` crash). **Fidelity gate PASSED** (`fidelity-capture.mjs` diff, ours vs mockup `:5174`): top-right chrome `search → notifications → help → user-menu → chatter-toggle` matches the mockup exactly; top-left = brand + switch-app + top menu. Placement confirmed mechanically, not by eye.

### 1.3 Data views (presentational, mock data) — match mockup density
> **Scope reality (checked 2026-06-02):** the CORE views already exist and are **backend-wired** — `views/{ListView,FormView,DataPage,AggregatePanel,data-view-context}` + `views/page/{Action,Column,Field,Group}` DSL + `toolbars/DataToolbar`. They have **no stories** and are **un-fidelity-diffed**. So 1.3 for the core = (a) add Storybook stories with mock data/providers, (b) **fidelity-diff our live app `:5173` vs mockup `:5174`** for list + form to surface the placement/functional gaps mechanically (the user's concern — sort cycle, pager modes, in-header column selector, Save/Discard-on-top, density), (c) close gaps. **MISSING (build new):** BoardView, CalendarView, GalleryView, GanttView, TimelineView, TreeView, GraphView (needs @xyflow/react — deferred deps), DashboardView, ResourcePage, CollectionDataToolbar. **Next slice:** core-view stories + the list/form fidelity-diff (login alice on `:5173`) → gap list → close. Functional behavior (chain agg, pager wiring) overlaps STAGE 2.
>
> **Fidelity diff RUN (2026-06-02, our `:5173/notes` vs mockup `:5174/notes`) — concrete gaps found:**
> - **Pager / filter / search MATCH** — ours has `Records 1-50 / 10052 · Previous · Next` + filter + global search, same placement as the mockup. ✓
> - **GAP A — column chooser ("Visible fields"): CLOSED** (`DataToolbar` `VisibleFieldsMenu` on `ui/dropdown-menu` + TanStack `columnVisibility` in `ListView`, last-column guard in UI + logic). Verified on live `:5173/notes` (login alice): rows still load (`Records 1-50/10052`), control present, opens with 5 column toggles, no crash. **Caught + fixed an interaction-only bug Codex shipped** (typecheck-clean but crashed on open: `Menu.GroupLabel` outside a `<DropdownMenu.Group>` → "MenuGroupContext is missing") — wrapped the label+items in a Group. Lesson: wired/overlay changes need an OPEN-interaction gate, not just typecheck + render.
> - **Chain aggregation — VERIFIED PRESENT (Stage-1 UI).** The "Filter, group, favorites" popover (live `:5173/notes`) opens cleanly (3 columns: Filters + custom, **Group by → "Add custom group"** = chained/stacked group levels, Favorites → save search). Code: `groupStack` + `DataViewGroupGranularity` + `onGroupStackChange`. So chained group-by is wired. **Stage-2 human-QA still owed:** group by year→month with backend bucket counts/totals + nested collapsible rows + aggregate footer (preservation gates) — verify against real data.
> - **All three user-named concerns addressed:** chain aggregation (present), pager (matches mockup), column selector (GAP A, built+working).
> - **GAP C — GROUPED LIST VIEW (advanced) — SEPARATE SLICE, Odoo-parity (user, 2026-06-02).**
>   **✅ BACKEND DONE (2026-06-02, committed `64a679b`): the per-bucket filter echo now ships UPSTREAM.** `strawberry-django-aggregates>=0.4.1` grew an opt-in `enable_filter_echo=True` (each grouped bucket exposes `filter: JSON!` shaped like `notes(filters:)`, resolving the value from the live filter type so a choices column exposed as a GraphQL enum echoes its **wire name** `DRAFT`, not the stored `draft`). The local `GroupedFilterEcho`/`aggregates.py` we'd prototyped is dropped — upstream owns it. Wiring: `enable_filter_echo=True` on the note `AggregateBuilder`; every group-by axis needs a matching list-filter field, so `updated_at` was added to `NoteFilter`. Verified: `noteGroups{ results{ filter } }` round-trips back into `notes(filters:)` (status enum + day-range), e2e 33/0, schema --check ok. **STILL OPEN (frontend):** SDK (bucket-filter → per-group `useResourceList`; group + per-group pagers) → ListView two-pager + fold UI. The grouped Kanban LANE rendering landed separately (`3ea3977`); the list-view fold/two-pager UX is the remaining piece. Required behavior:
>   1. **Groups folded (collapsed) by DEFAULT**; expanding a group lazily loads its rows.
>   2. **Top-level pagination paginates the GROUPS** (not the flat item list).
>   3. **Each group has its OWN pagination** of items inside it (two pager levels).
>   4. **Aggregates per group** on the group header (sums/counts/…).
>   5. Nested/chained groups (year→month) carry all of the above.
>   Extend with **feature parity vs Odoo 18 groups + UX** — full spec in **`.agents/notes/grouped-list-odoo-parity.md`** (research DONE). **Key findings:** our grouping today is *client-side partitioning of one flat page* (`ListView.tsx:899`) with the top pager on ITEMS; parity requires moving grouping **server-side** (top pager pages the group buckets; each expanded group lazily fetches its own paginated rows). EXISTS: group-bucket pagination + per-group count + per-group aggregates in the backend (`strawberry-django-aggregates`) + `useResourceGroupBy` (group-level page). **The big MISSING piece: per-group PAGINATED items** — `<Model>Grouped` has only `{key,count,measures}`, NO records-in-group field; and the lib's multi-axis group-by is FLAT composite rows, not Odoo lazy nested buckets. **ARCHITECT DECISIONS — MADE (user, 2026-06-02):** **(1)** Implement the **backend** to support BOTH a **per-query** path and a **composite** path. **Per-query is primary** because a group can hold ~1,000,000 items — a single composite query is unviable; we must paginate *through the groups* (top pager) AND lazily paginate *each group's items*. So the backend echoes a **per-bucket filter** (Odoo `__domain` analogue) so the client deterministically fetches each group's page of items; composite (items embedded) stays an option for small sets. **(2)** Nested grouping = **recurse** (one grouped query per expanded parent, lazy) for scale, with the flat-composite fetch as the small-set option. **Lib reality:** `strawberry-django-aggregates` is a pinned PyPI dep (NOT editable) but its `AggregateBuilder` already exposes `filter_type` (the grouped query accepts a `filter`), `get_queryset`, and `pagination_style` (offset/cursor/both). So the per-bucket filter echo + per-group items must be added at the **Angee level** (wrap/augment the generated `<Model>Grouped` type + a per-group items resolver), never by editing `.venv`. **BUILD ORDER:** backend per-bucket filter echo (enables per-query) → SDK (bucket-filter → per-group `useResourceList`; group + per-group pagers) → ListView two-pager + fold UI. Backend first. Also: per-group measures need `aggregate_fields` on a summable model (notes' `word_count` is a Python property → demo likely needs a `Sale`-like model); and REBAC field-gating differs between aggregate buckets and the scoped item list (counts could diverge from visible rows). Build only after the architect sets (1) + (2).
> - **GAP B — view-switcher kinds (lower priority, alternate visualizations):** ours = `List · Board` (DataViewKind, 2); mockup = `List · Kanban · Form · Calendar · Pivot · Graph` (6). → extend `DataViewKind` + build the missing view kinds (Graph needs the deferred `@xyflow/react`).
> - Tool now supports `--login=<user>` (live login on `:5173/login`) and `--storage=<path>` for authenticated app captures.
- [ ] done [ ] verified [ ] checked by human (final qa) — ListView: dense rows, collapsible grouped rows w/ counts, status badges, tag chips, avatars, column chooser, selection bar, aggregate footer, **column sort cycle (asc→desc→none, aria-sort), row-link nav (click/Enter/modifier), skeleton/empty/error/filtered-empty states, indeterminate select-all** + stories
- [ ] done [ ] verified [ ] checked by human (final qa) — DataToolbar/CollectionDataToolbar: search, removable chips, custom filters, group-by + granularities, favorites, view switcher, Pager (list range + page-size popover; form record-number; prev/next) + stories
- [ ] done [ ] verified [ ] checked by human (final qa) — DataPage/ResourcePage: `Page→Header→ControlBand→Body`(navigator aside + content); record placement inline/drawer + stories
- [x] done [x] verified [ ] checked by human (final qa) — **FormView dirty-save parity** (user flagged twice). Was: Save a bottom button, always shown, NO Discard. Now: **Save+Discard in a top control band** (`FormActions`, left), **dirty-only** via `form.Subscribe` on `state.isDirty`, **Discard** = `form.reset()` to baseline, **Save** = `form.reset(savedValues)` (baseline → saved, no flash/revert), inline **ErrorBanner** (was an inline `<p>`). **VERIFIED on live `:5173/notes/<id>` (alice):** clean form → 0 Save/Discard; edit → both appear at y=254 (top band, not bottom); Discard → reset to clean; Save → buttons clear + no error + **reload confirms the edit persisted** (no revert). Typecheck green.
- [x] done [x] verified [ ] checked by human (final qa) — **FormView chrome part 1: unsaved-changes nav guard + inline editable title + FormView story.** Nav guard = TanStack Router `useBlocker` gated on `form.state.isDirty` + the `ModalsHost` confirm; inline title binds the `title:true`/`name==="title"` field (`Input` in edit, H1 read-only). **Verified live:** clean form navigates freely; edited form → "Unsaved changes — leave without saving?" (Stay/Leave) confirm + navigation BLOCKED; title input shows the record title ("Physical great") in edit mode. Also fixed a stale `ConsoleShell.smoke.test` (asserted the pre-rebuild TopMenu "All notes" tab; TopMenu is now data-driven links) — base suite 26/26 green. (Smart-buttons / More menu / notebook tabs deferred — need record-relation data.)
- [x] done [x] verified [ ] checked by human (final qa) — **FormView SHEET LAYOUT/DESIGN refactor (user, 2026-06-02: "formview layout design has not been refactored either yet").** The form was a plain stacked-field list; now it's a record SHEET matching the mockup `:5174/notes/:id`: centered sheet surface + **title row** (inline title + **mono record subtitle** id·created·updated·words + **status stepper** moved into the row + **star/share** icon actions), a **2-col label-above-value field grid** (composes `FieldRoot`/`FieldLabel` + `FormGrid`/`FormSectionKicker`, full-width tag fields, inline "Odoo-style" editable field look), and a **Notebook** (`ui/tabs`) whose first tab hosts the body/long-text field, with a `FORM_VIEW_NOTEBOOK_SLOT` extension seam for addon-contributed tabs. Added a declarative `body?` field marker (mirrors `title`). Committed `87d7415`. **Full cadence ran:** Codex lift → arch+react reviewers (both flagged: `<label>`-nesting a11y regression, dead `fieldSpan` cast, un-exported slot key, unsafe `errors.join`, name collision, raw lucide Share, name-sniffed body) → Codex rework fixed all 8 → re-verified: base+storybook typecheck, base vitest 26/26, **story render** (`test-results/fidelity/form-ours-story2.png`) + **live `:5173` real-data render + dirty-save OK** (`form-ours-live.png`), visual fidelity vs mockup `:5174` confirmed. **Flagged follow-ups (commented in code, separate slices):** (a) `EDITABLE_FIELD_CONTROL_CLASS` reaches into widget child-DOM → proper fix is a widget `appearance="seamless"` variant on `WidgetRenderProps`; (b) star/share are presentational placeholders pending action wiring (with the deferred smart-buttons); (c) subtitle reads conventional `Row` field names (untyped) until `Row` carries typed metadata.
- [ ] done [ ] verified [ ] checked by human (final qa) — **FormView record pager scopes to the list's filtered slice (user, 2026-06-02).** When a form is opened from a filtered/sorted list, its Previous/Next **record** pager must navigate **within that filtered+sorted slice** and show "N of <filtered total>" — NOT the whole table (today it shows the full DB, e.g. "1 of 10,052"). Requires the form to inherit the list's **DataView query context** (filter/sort/search/group), so the prev/next record = the neighbor in that query and the total = the query's count. Crosses the form↔data-view boundary (shared DataView state / URL state / SDK query); coordinate with the data-view query work (overlaps the grouped-list + seamless-cache slices). Distinct from the nav-guard/inline-title chrome above.
- [ ] done [ ] verified [ ] checked by human (final qa) — FormView full chrome remainder (smart-buttons, More/Actions menu, notebook tabs) + stories (gate against the mockup fidelity fingerprint)
- [ ] done [ ] verified [ ] checked by human (final qa) — BoardView/CalendarView/GalleryView/GanttView/TimelineView/TreeView/GraphView/DashboardView + stories

### 1.4 Widgets (presentational triplets edit/read/cell)
- [x] done [x] verified [ ] checked by human (final qa) — registry + text/textarea (existing) + **scalar batch: email/url/phone/integer/float/json/boolean/booleanToggle/selection** — `{edit,read,cell}` triplets on `ui/{number-field,input,checkbox,switch,select,textarea,code,text-link}`, registered by kind. Verified: base+storybook typecheck green, **base vitest 26/26**, zero provenance, `Widgets/Scalar Fields` story renders all in edit/read/cell (url read → link). Remaining: combobox.
- [ ] done [ ] verified [ ] checked by human (final qa) — date/datetime (+internals), selection/combobox, markdown editor+preview (CodeMirror)
- [x] done [x] verified [ ] checked by human (final qa) — statusBadge/ribbon/progressBar/ownerCell/themePicker/date/combobox/many2one/many2many (+ existing statusbar/tagInput) + stories. Triplets on `ui/{badge,avatar,select,chip,calendar,slider,number-field,popover}`. Verified: base+storybook typecheck, base vitest 26/26, zero provenance, the `Widgets/Scalar Fields` matrix renders all 18 widgets edit/read/cell (statusBadge→toned badge, progressBar→bar, ribbon→chevron stages, ownerCell→avatar, themePicker→swatches, many2many→chips). **Stage-2 decision (flagged, not blocking):** `many2one`/`many2many` are presentational (options via `WidgetField.options`); decide framework `optionsLoader` hook vs per-addon field-meta options when wiring relations.

### 1.5 Communication / feedback / preview / upload / cells
- [ ] done [ ] verified [ ] checked by human (final qa) — Chatter (aside/tabs/composer/counts/RevisionTimeline) + Chat primitives + stories
- [x] done [x] verified [ ] checked by human (final qa) — **ModalsHost / Toast + stories.** ModalsHost pre-existed; added the **Toast** surface (`feedback/Toast.tsx`) on `@base-ui/react/toast` (Base UI owns queue/timers/stacking/focus/live-region a11y), mirroring ModalsHost (base owns the queue+hook+renderer, no SDK split): `ToastProvider` + `useToast()` imperative API (intents info/success/warning/error → `lib/tones` color + `chrome` icon; per-intent durations; error/warning persist; close + **action that dismisses**; error→high priority). Mounted app-wide in `createApp` beside ModalsHost; close label localized (`toast.dismiss`). Committed `6ba7c23`. **Full cadence ran:** Codex build → arch+react reviewers (both: deleted unearned `onExternalEvent`/`limit` seams + dead provider `timeout`; collapsed unreachable intent fallback; **action now dismisses** the toast; localized aria-label; removed an unsound `as ToastApi` cast/mutation; reconciled the Base-UI stacking `z-index` vs the no-arbitrary-z doc rule) → Codex rework fixed all 9 → re-verified: base+storybook typecheck, base vitest **27/27** incl. a new `Toast.test.tsx` provider-contract render harness, **interactive render** (fires on click, no console errors) + **action-dismiss** confirmed (`test-results/fidelity/toast-*.png`). Deferred: backend-events→toast bridge (a later wiring slice; seam intentionally NOT pre-carved).
- [ ] done [ ] verified [ ] checked by human (final qa) — `preview/`(5) · `upload/`(4) · `cells/`(6) + stories

### 1.6 Addon UI (presentational) — per addon
> **Placement — DECIDED (architect, 2026-06-01):** addon UI lives at
> **`src/<namespace>/<addon>/web/`** — co-located with each addon's backend, exactly like
> the existing notes consumer addon (`examples/notes-angee/src/example/notes/web/`). So the
> lifted framework addons become full-stack units, e.g. `src/angee/iam/web/`,
> `src/angee/storage/web/` (backend Python beside `web/` frontend). Base/framework UI
> primitives stay in `packages/base`; addon UI composes `@angee/base` + `@angee/sdk`.
> Skip empty stubs: connect/integrate/money/sequence/uom.
> **Note:** P1's `auth` UI maps to this repo's **`iam`** addon (label `iam`), so its UI
> root is **`src/angee/iam/web/`**, not `auth/web`. See `.agents/plans/iam-roles-lift.md` (D6).
- [ ] done [ ] verified [ ] checked by human (final qa) — auth/iam UI (login + fragments + views) — ~31 files → `src/angee/iam/web/`; backend lift in `iam-roles-lift.md`
- [ ] done [ ] verified [ ] checked by human (final qa) — storage UI (views + widgets + upload) — ~37 files
- [ ] done [ ] verified [ ] checked by human (final qa) — operator UI (views) — ~31 files
- [ ] done [ ] verified [ ] checked by human (final qa) — agents UI (Chat + views + fragments) — ~21 files
- [ ] done [ ] verified [ ] checked by human (final qa) — integrate-auth-oauth UI (views + fragments) — ~18 files
- [ ] done [ ] verified [ ] checked by human (final qa) — angee UI (views) — ~14 files
- [ ] done [ ] verified [ ] checked by human (final qa) — knowledge UI (views) — ~4 files

## STAGE 2 — Wire to backend (features per P1)
> Gated by **D-A** (nuqs vs TanStack Router search). Dissect P1 for behaviour/contracts.
- [ ] done [ ] verified [ ] checked by human (final qa) — SDK contracts: data-view state · aggregates/group hooks · model-fields metadata · providers/runtime · persisted ops · feedback queue (reconcile with current SDK)
- [ ] done [ ] verified [ ] checked by human (final qa) — Wire list/group/aggregate/pagination + form read+mutate + toolbar filters to data
- [ ] done [ ] verified [ ] checked by human (final qa) — Wire widgets to field metadata; `notes.wordCounter` override (live body + backend aggregate)
- [ ] done [ ] verified [ ] checked by human (final qa) — Wire each addon to its backend (routes/menus/queries/mutations/roles/i18n)

### Stage 2 acceptance — P1 regressions to preserve (human-QA gates)
- [ ] done [ ] verified [ ] checked by human (final qa) — Column visibility editor; last visible column cannot be hidden
- [ ] done [ ] verified [ ] checked by human (final qa) — Chained group-by (year→month) with backend bucket counts/totals + forwarded granularity
- [ ] done [ ] verified [ ] checked by human (final qa) — Pager: list-mode range `1-50 / N` + page-size popover (10/20/50/80/100/200, default 50) and form-mode record number `N / total`, both with prev/next paging — page position always visible
- [ ] done [ ] verified [ ] checked by human (final qa) — Nested/collapsible group rows with stable keys + summaries
- [ ] done [ ] verified [ ] checked by human (final qa) — Form save: dirty-only controls, no flash/reload, no revert, baseline resets to saved value (button placement + dirty-only DONE `1e71bc4`; the cache-update half below is the open bug)
- [ ] done [ ] verified [ ] checked by human (final qa) — **SEAMLESS POST-MUTATION UPDATE across ALL forms, views, AND subscriptions (FRAMEWORK-LEVEL, currently BROKEN).** After any save/mutation the UI must reflect the new values **with no page reload**. Today (user-reported, 2026-06-02): on save the form fields **revert to previous/undefined values and only show the new values after a manual reload**. This is the SDK's urql graphcache wiring — the mutation result must normalize into the cache (and invalidate/refetch affected queries) so every bound form, list/view, and live subscription updates in place. **P1 fixed this once (`45c492557 page reload fix for saving`).** Foundational: it affects every addon's every form+view+subscription, so fix it in the SDK cache/mutation layer, not per-form. Verify by editing→saving on the live app and asserting the value updates WITHOUT reload (the e2e form spec carries a `fixme` documenting it until then).
- [ ] done [ ] verified [ ] checked by human (final qa) — New/empty form derivation: single header title/status, no duplicate body/header
- [ ] done [ ] verified [ ] checked by human (final qa) — Notes word count (live body widget + backend aggregate totals)
- [ ] done [ ] verified [ ] checked by human (final qa) — Markdown editor: commands, toolbar, link input, keybindings, preview
- [ ] done [ ] verified [ ] checked by human (final qa) — Visual/layout fidelity vs mockup (density, real header, no empty band, focus/border)

## Carryover — orthogonal dep upgrades (not gating the lift)
- [ ] done [ ] verified [ ] checked by human (final qa) — `vite 6→8` + `@vitejs/plugin-react 4→6` · `vitest 3→4` + `happy-dom 16→20` · `typescript 5.9→6`
- [ ] done [ ] verified [ ] checked by human (final qa) — `@urql/core 5→6` + `@urql/exchange-graphcache 7→9` · `lucide-react 0.469→1.17` · `@types/node 22→25` · `@graphql-codegen/cli`
- [ ] done [ ] verified [ ] checked by human (final qa) — Backend `uv` stack: inventory (`uv tree --outdated`) + scoped upgrade plan

---

# P1 UI Inventory and Lift Checklist

Status: inventory plan, not implementation.

Reference checkout: `../angee-django-p1`.

Resolved P1 host path: `../angee-django-p1/examples/angee-notes/web`.
The user-provided path `../angee-django-p1/examples/notes/web` is stale in the
P1 checkout available from this workspace.

Target repo: this repository, `/Users/alexis/Work/angee/angee-django`.

## Scope

This checklist tracks every React surface reachable from the P1 notes host app:

- the host entrypoint and host-only demo login slot;
- addon manifests, routes, menus, widgets, chatter tabs, slots, and i18n;
- base framework chrome such as app rail, topbar, breadcrumbs, systray, user
  menu, chatter, page shells, toolbar controls, and modals;
- all view primitives used by notes and the base addons, including list, board,
  form, calendar, gallery, gantt, timeline, tree, graph, dashboard, pagination,
  group-by, filters, favorites, custom filters, aggregators, and bulk actions;
- SDK runtime contracts and providers that make those components work;
- generated runtime metadata/operations consumed by the UI, recorded as
  dependencies only. Generated runtime stays output, not source.

Important interpretation: the import closure includes all exports reachable
through `@angee/base` barrels. Some components are exported and available but
not mounted by the P1 notes host on first load. They are still included because
the lift target is the reusable framework surface, not only the visible notes
screen.

## Owner Map

- Framework-owned UI belongs under `packages/base/src` in this repo.
- Framework-owned headless contracts, routing, persisted operations, GraphQL,
  data-view state, auth, and field metadata belong under `packages/sdk/src`.
- Addon product surfaces belong under their source addon packages, not under the
  host app. P1 has top-level `addons/*/ui/base`; this repo currently does not.
- The notes example addon belongs under the example/addon source, not generated
  `runtime/`.
- Generated operation names and field metadata are dependencies to regenerate
  from source contracts, not files to edit by hand.

## Import Closure Snapshot

- Entrypoint: `../angee-django-p1/examples/angee-notes/web/src/main.tsx`.
- Internal source files reached: 462.
- Breakdown:
  - `packages/base`: 270 files.
  - `packages/sdk`: 19 files.
  - `packages/base-previews`: 13 files.
  - `addons/agents`: 20 files.
  - `addons/angee`: 14 files.
  - `addons/auth`: 31 files.
  - `addons/integrate-auth-oauth`: 18 files.
  - `addons/knowledge`: 3 files.
  - `addons/operator`: 30 files.
  - `addons/storage`: 29 files.
  - notes example addon: 11 files.
  - host web: 2 files.
  - generated runtime metadata/operations: 2 files.

Current-repo gap observed during inventory:

- No top-level `addons/` directory exists in this repo.
- No `packages/base-previews` package exists in this repo.
- `packages/base/src` is much smaller than P1 and misses the majority of the
  chrome, toolbar, view, widget, feedback, preview, upload, and fragment
  surface.
- `packages/sdk/src` exists but differs materially from P1 and lacks the P1
  data-view, persisted operation, runtime provider, auth provider, route, field
  metadata, feedback, permission, CSRF, and storybook contracts listed below.

## Known P1 Fixes and Regressions To Preserve

These are explicit preservation checks from P1 git history plus current visual
regressions observed in this repo. Treat them as acceptance criteria for the
lift, not optional polish.

| Done | P1 history signal | Surface | Preservation check |
| --- | --- | --- | --- |
| [ ] | `74f4c6b80 ListView: column editor in the table header (page mode), not a separate bar`; `aef72e899 checkpoint: add list sorting and column controls` | Column selector | List/table views must expose the column visibility editor from the table header/page mode. The user must be able to choose visible columns, and the last visible column cannot be hidden. |
| [ ] | `7b31cf12d sdk: backend aggregation consumption`; `e68d319bf base: declarative <Column aggregate>`; `84e087933 base: ResourcePage feeds backend group buckets`; `69bdd753d frontend: address review — forward search/granularity, wire footer total` | Aggregates and group chains | Data views must support chained grouping, e.g. aggregate by year and then within that by month, with backend bucket counts/totals and selected date granularities forwarded through the query. |
| [ ] | `45b9fa0da fix notes server-side grouping integration`; `5e4d89d8b feat(DataView): grouped rows with collapsible carets + per-group summary` | Nested group rows | Group rows must be nested/collapsible with stable keys, summaries, and aggregate values, not a single flat group. |
| [ ] | `b53a8fb63 Extract base pagination primitive`; P1 `packages/base/src/ui/pagination.tsx` + `views/data-view-model.ts` | Pager / page number | The pager has **two modes**, both showing position (not just a flat record range): **list** mode shows the record range `1-50 / N` with a **page-size popover** (options 10/20/50/80/100/200, default 50) and prev/next; **form** mode shows the record number `N / total` with prev/next to page through records. The current page / record position must always be visible. |
| [~] | `890a05111 Fix form save regression: send {id, input} with writable fields only`; `45c492557 page reload fix for saving`; `0d636d65c checkpoint commit - notes saving` | Form save behavior | Save and Discard controls appear only when the form is dirty. Saving must not flash/reload the form, must not revert to the initial value, and must reset the dirty baseline to the saved value. **(Dirty-only + placement DONE `1e71bc4`; the no-revert/no-reload half is the open cache bug — next row.)** |
| [ ] | `45c492557 page reload fix for saving` (P1 fixed it once); SDK urql graphcache / invalidation layer | **Seamless post-mutation update (ALL forms/views/subscriptions)** | After save/mutation the UI reflects new values with **NO page reload**. CURRENTLY BROKEN (user-reported 2026-06-02): saved fields **revert to undefined; new values appear only after manual reload**. Fix in the SDK cache/mutation wiring (normalize the mutation result into urql graphcache + invalidate/refetch affected queries) so it holds for every form, every view, and every live subscription — not per-form. Framework-level, highest-leverage data-correctness gate. |
| [ ] | mockup `/notes/1/edit --dirty` fingerprint (`Discard`,`Save` → band=top side=left); P1 `views/FormView.tsx` `FormViewToolbar`; mockup `pages/NotesForm.tsx:121-133` | Form action placement | Save/Discard render in the **top control band, left group** (beside Actions) — NOT in a bottom action bar. The control band's right group holds smart-buttons → record pager → view-switcher. Verify with the fidelity fingerprint, not by eye. |
| [ ] | `83b7bae67 Notes views: restore NoteListView/NoteFormView; fix empty-form derivation`; `db161b717 Notes form: editable title, single status, audit fields, plain word count` | Form derivation | New/empty record forms must derive their editable field state correctly and render a single header status/title, not duplicate body/header fields. |
| [ ] | `8c1ec64b1 Add readonly widget chrome and note word counter`; `98cd86aef notes form fields: drop bogus owner, bind word-count to real field`; `62478b4c5 notes: render backend word-count aggregate, drop client reduce` | Notes word count | Notes must override the word-count field with widget id `notes.wordCounter`. The form widget must read the live markdown body through `useFormState`, while list/group totals must use backend aggregate data. |
| [ ] | `f4c39fb66 Lift markdown CodeMirror commands`; `d01ede91c Implement plan 010 markdown baseline` | Markdown editor | Markdown editor must keep CodeMirror command wiring, toolbar buttons, link input, keybindings, and preview behavior. |
| [ ] | `30bff7deb Fix notes dataview layout controls`; `5627390e1 feat(ui): deferred A.2-A.4 upgrades + visual fixes`; `fc598eb92 refactor(base): land the react reviewer findings (27 items)` | Visual/layout fidelity | The lifted UI must preserve the P1 layout pass. Current observed regression: the Body label/markdown editor area is broken, with duplicate Body text, cramped toolbar chrome, and focus/border layout that does not match the refined P1 form controls. |
| [ ] | `dbd0fbcd7 fix(scenes): grid-area placement + FormView toolbar shape`; `4afbe9c1d Add page layout primitives`; `bc531269e Add base form layout fragments` | Page/form layout | Form chrome, toolbar placement, field groups, page sections, and grid-area placement must match the P1 component layout, not ad hoc local page structure. |
| [ ] | P1 `views/ListView.tsx:890-934` (`ColumnHeader`/`nextDirection`) | Column sort cycle | Sortable column headers cycle asc → desc → none on click, sort exactly one column at a time, and expose `aria-sort` + a direction caret. |
| [ ] | P1 `views/ListView.tsx:1254-1333` (`DataRow` linkProps) | Row-link navigation | Rows with a record route are keyboard-focusable links: click/Enter navigates, modifier/middle click opens a new tab, and clicks on in-cell controls (checkbox/button/link) do not navigate. |
| [ ] | P1 `views/ListView.tsx:675-683,803-818,936-1023` | List loading/empty/error states | List views render four distinct states — initial skeleton (table- and group-shaped), empty-state slot, error slot, and a filtered-empty ("No rows match") vs never-had-rows ("No rows yet") body message — never a blank table. |
| [ ] | P1 `views/ListView.tsx:640-645` (`displayTotal`) | Filtered total count | The toolbar/pager total reflects the *filtered* result count in client mode (search/filter active → filtered count) and the backend total in server mode. |
| [ ] | P1 `views/ListView.tsx:684-722` + `views/data-view-model.ts:63-106` | Selection scope reset | An active row selection clears when the result scope changes (search/filter/custom-filter/group/sort/page/page-size) so bulk actions never run on a stale selection. |
| [ ] | P1 `views/ListView.tsx:769-775` | Select-all indeterminate | The header select-all checkbox renders indeterminate when a subset of page rows is selected and toggles the whole page on click. |
| [ ] | P1 `toolbars/DataToolbar.tsx:527-616,790-824` (`CustomFilterEditor`) | Custom-filter type adaptation | The custom-filter editor derives operator choices and the value control from the selected field's type (text/number/date/selection/relation/boolean) and omits the value input for `isEmpty`/`isNotEmpty`. |
| [ ] | P1 `views/FormView.tsx:420-431,661-677` (`FormUnsavedChangesGuard`) | Unsaved-changes guard | Navigating away from a dirty form prompts an unsaved-changes confirm (Stay/Leave) via the routing navigation blocker; a clean or read-only form does not block. |
| [ ] | P1 `views/FormView.tsx:322,350-355,459-463` (`saveError`) | Save-error banner | A save failure shows an inline error banner above the form (the mutation error message) that clears on the next save or record change. |
| [ ] | P1 `views/FormView.tsx:474-536,564-578` | Inline title / statusbar placement | The title renders as an inline borderless H1 input (edit) / H1 (read-only) in the header; statusbar-semantic fields render only top-right; a body `<Field>` referencing the title or a statusbar field is suppressed (extends the "Form derivation" gate). |
| [ ] | P1 `views/FormView.tsx:737-887` (`FormViewToolbar`/`MoreMenu`) | Form record nav + overflow | The form toolbar shows record position "N of total" with prev/next record paging and an overflow ("More") menu for secondary/danger actions. |
| [ ] | mockup `molecules/Statusbar.tsx:54-85` | Statusbar stage widget | The statusbar/ribbon stage widget renders clickable todo/done/active chevron steps (role tablist/tab) that set the record's stage on click — not a static badge. |
| [ ] | mockup `molecules/ThemePick.tsx:41-73` | Theme picker behavior | The theme picker offers Light/Dark/System swatches that immediately toggle `data-theme` and persist the choice; System shows a split preview. |
| [ ] | mockup `molecules/SmartButtons.tsx:15-58` | SmartButtons | Detail pages can show a SmartButtons row of related-record count shortcuts that navigate to the matching collection/tab on click. |
| [ ] | mockup `molecules/PageTabs.tsx`, `Notebook.tsx` | Page tabs vs notebook | Page-level tabs (per-tab counts, doubling as the section divider) and in-card Notebook tabs are preserved as two distinct tab strips, each switching its panel on click. |

## Top-Level Completion Checklist

- [ ] Re-run the import closure from `examples/angee-notes/web/src/main.tsx`
      before implementation and confirm no P1 source file is missing from this
      inventory.
- [ ] Decide the target package layout for top-level P1 addon UI packages in
      this repo before moving code.
- [ ] Lift SDK contracts first, because base views and addon pages depend on
      them.
- [ ] Lift `packages/base` primitives before higher-level shell/view code.
- [ ] Lift shell/chrome before addon route pages that assume menus, slots,
      chatter, page meta, role gates, and app routing.
- [ ] Lift data-view state, toolbar, ListView, FormView, and DataPage together;
      they are a coupled surface.
- [ ] Lift widget registry and all field widgets before addon forms/lists.
- [ ] Lift base addon manifests and pages only after the reusable framework
      surface compiles.
- [ ] Recreate the notes example addon source rather than editing generated
      runtime.
- [ ] Regenerate runtime operations/field metadata from source contracts after
      lift.
- [ ] Audit the P1 git history above before implementing each affected surface;
      do not reintroduce regressions that P1 already fixed.
- [ ] Verify all route paths, menu ids, widget ids, slot ids, chatter ids, and
      persisted operation names match P1 exactly or are intentionally renamed in
      one owner.
- [ ] Confirm every file in the SDK and base appendices is either lifted,
      intentionally deleted with an owner-level rationale, or replaced by a
      clearly equivalent owner in this repo.

## Host Composition Inventory

| Done | P1 source path | Surface | Current behavior | Lift target |
| --- | --- | --- | --- | --- |
| [ ] | `examples/angee-notes/web/src/main.tsx` | Host app entrypoint | Imports addon manifests, generated `fieldMeta`/`operations`, demo auth slots, and `createApp`; registers addons in order `notes`, `auth`, `storage`, `agents`, `angee`, `integrate`, `knowledge`, `operator`; mounts the app into `#root` with public/console shells and `/graphql/`. | Example host source in this repo. |
| [ ] | `examples/angee-notes/web/src/demo-auth.tsx` | Host login slot | Contributes `AUTH_LOGIN_SLOT` footer region `angee-notes.demo-credentials` with admin/admin, alice/alice, bob/bob hint. | Example host source in this repo. |
| [ ] | `examples/angee-notes/runtime/__generated__/field-meta.ts` | Generated field metadata | Supplies model field descriptors, labels, widgets, enum options, relation metadata, GraphQL names, semantic hints, and choice tones used by `<Field>`, `<Column>`, and widgets. | Regenerated output only. |
| [ ] | `examples/angee-notes/runtime/__generated__/operations.ts` | Generated persisted operations | Supplies query/mutation/subscription registry used by SDK persisted operation hooks. | Regenerated output only. |

## SDK Runtime and Contracts

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/sdk/src/define-addon.ts` | Addon manifest owner | Defines `AddonManifest`, `AddonRoute`, `MenuItem`, widgets, i18n, icons, chatter, and slot contribution merging. Dedupes/overrides deterministic contributions, sorts by sequence, and fails on route/widget/icon/i18n collisions. |
| [ ] | `packages/sdk/src/contracts.ts` | Shared UI contracts | Owns `MenuItem`, `WidgetTriplet`, `WidgetProps`, `DataViewState`, `DataViewFilter`, `DataViewGroupSpec`, `FieldMeta`, `AuthState`, `AuthUser`, and route/menu/chatter/slot contract shapes. |
| [ ] | `packages/sdk/src/runtime.tsx` | Runtime context | Provides addon-composed widgets, menus, i18n, icons, chatter contributions, slots, and field metadata to framework/base components. |
| [ ] | `packages/sdk/src/providers.tsx` | Hook facade | Exposes `useWidget`, `useMenus`, `useSlot`, `useAuth`, `AuthProvider`, `useT`, `useNamespaceT`, `FormProvider`, `useFormState`, and `useFieldMeta`. |
| [ ] | `packages/sdk/src/data-view.tsx` | Data-view state owner | Owns search, active filters, custom filters, group stack, sort, view kind, page, page size, and saved favorites. Default page size is 50. Saves favorites to `localStorage` key `angee:data-view:<resource>:favorites`. Setters reset page. |
| [ ] | `packages/sdk/src/model-fields.ts` | Field metadata lookup | Resolves fields by model and field name/label/type/short name. Supplies widget defaults, enum choices, GraphQL names, relation metadata, semantic hints, and tone metadata. |
| [ ] | `packages/sdk/src/operations.ts` | Operation registry contracts | Defines persisted operation registry shapes consumed by `PersistedOperationsProvider` and query/mutation hooks. |
| [ ] | `packages/sdk/src/persisted.ts` | Persisted operation helpers | Resolves operation definitions and names from generated operation maps. |
| [ ] | `packages/sdk/src/graphql-context.ts` | GraphQL client context | Provides urql client/runtime context for persisted hooks. |
| [ ] | `packages/sdk/src/graphql-client.ts` | GraphQL client factory | Creates/configures the urql GraphQL client used by provider/hooks. |
| [ ] | `packages/sdk/src/graphql-provider.tsx` | Persisted operations provider | Wraps urql, tracks active persisted queries, supports `resetClient`, `registerQuery`, `invalidateQueries`, and optional model-event invalidation. |
| [ ] | `packages/sdk/src/graphql-hooks.ts` | Persisted hooks | Implements `usePersistedOperation`, `usePersistedQuery`, `usePersistedMutation`, `usePersistedSubscription`, invalidation hooks, login/logout cache reset, CSRF/session clearing, and runtime auth-state mapping. |
| [ ] | `packages/sdk/src/aggregates.ts` | Aggregate/group owner | Provides `autoExtractGroupBy`, `autoExtractAggregate`, `selectMeasure`, `useResourceGroupBy`, and `useAggregateQuery`; lets views use backend group counts and aggregate buckets rather than page-local reductions. |
| [ ] | `packages/sdk/src/i18n.ts` | SDK i18n helpers | Shared namespace/key lookup helpers for addon and framework translations. |
| [ ] | `packages/sdk/src/feedback.ts` | Confirm/prompt queue | Defines queued confirm and text-prompt provider contract, normalizers, and `confirmTypingMatches`. Rendered by base `ModalsHost`. |
| [ ] | `packages/sdk/src/routes.tsx` | Route helpers | Provides legacy/utility route registry surface used by base route composition. |
| [ ] | `packages/sdk/src/csrf.ts` | CSRF helpers | Reads/clears CSRF cookie and participates in login/logout mutation behavior. |
| [ ] | `packages/sdk/src/permissions.ts` | Permission helpers | Shared permission/role utility surface for UI role checks. |
| [ ] | `packages/sdk/src/storybook.tsx` | Storybook helpers | Provides runtime wrappers for isolated component previews/stories. |
| [ ] | `packages/sdk/src/index.ts` | SDK barrel | Re-exports SDK contracts and hooks used by `@angee/base` and addons. |
| [ ] | `packages/sdk/src/styles.css` | SDK styles | Shared SDK CSS entrypoint imported by consumers where needed. |

## Base Composition Root

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/createApp.tsx` | App composer | Composes addons, icons, foundation widgets, addon widgets, i18n, chatter, slots, and field metadata. Builds TanStack Router tree, default `/` redirect, shell wrappers, `RoleGate`, `RequireAuth`, `RuntimeRoot`, `AuthProvider`, `PersistedOperationsProvider`, and `ModalsHost`. |
| [ ] | `packages/base/src/addon-routes.ts` | Route composition helpers | Adapts addon route contracts to the concrete router and active route resolution. |
| [ ] | `packages/base/src/app/app-shell-context.tsx` | App shell context | Provides shell-level route/menu/page state used by console/public shells. |
| [ ] | `packages/base/src/app/menu-tree.ts` | Menu tree owner | Builds deterministic menu trees from addon menu contributions. |
| [ ] | `packages/base/src/index.ts` | Base barrel | Re-exports the framework UI surface used by host apps and addon UI packages. |

## Shell and Chrome

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/shell/ConsoleAppShell.tsx` | Console shell adapter | Reads menus and roles, derives rail/subnav/user/systray items, creates breadcrumb and chatter context from page meta, appends sign-out to user menu, and passes everything into `ConsoleShell`. |
| [ ] | `packages/base/src/shell/ConsoleShell.tsx` | Console chrome layout | Grid shell with rail, topbar, optional sidebar rail, breadcrumb row, content, and optional chatter aside. Hosts `ToastProvider`, `Notifications`, `ChatterProvider`, `TopMenu` variants, and breadcrumb trailing controls. |
| [ ] | `packages/base/src/shell/PublicAppShell.tsx` | Public shell adapter | Pass-through shell for public routes; public pages own their visual frame. |
| [ ] | `packages/base/src/shell/PublicShell.tsx` | Public login shell | Anonymous full-bleed login surface with background image, responsive hero/card grid, atmosphere, animated cube, card lead, and footer slot. |
| [ ] | `packages/base/src/shell/Shell.tsx` | Primitive shell | Named slots for header, navbar, main, and aside grid layout. |
| [ ] | `packages/base/src/shell/default-chatter.tsx` | Default chatter | Provides default `angee`, `comments`, and `activity` chatter placeholder contributions until addons override. |
| [ ] | `packages/base/src/shell/RevisionsChatterTab.tsx` | Activity placeholder | Default activity/revisions chatter tab placeholder. Notes overrides this by chatter id. |
| [ ] | `packages/base/src/chrome/AppRail.tsx` | App rail | Icon rail with start/center/end placement, active item, badges, tooltips, DnD reorder via `@dnd-kit`, keyboard Alt+Arrow reorder, optional persisted order, default app long-press, links and buttons. |
| [ ] | `packages/base/src/chrome/app-rail-model.ts` | App rail model | Deterministic ordering and movement helpers for rail items. |
| [ ] | `packages/base/src/chrome/AppBrand.tsx` | Active app brand | Renders active app icon/name link to the app root. |
| [ ] | `packages/base/src/chrome/TopBar.tsx` | Top bar primitive | Dark chrome row with slots and `TopBar.Spacer`. |
| [ ] | `packages/base/src/chrome/TopMenu.tsx` | Tab top menu | Horizontal nav using Base UI NavigationMenu and NavLink; supports links, text, and dropdown triggers. |
| [ ] | `packages/base/src/chrome/TopMenuDropdown.tsx` | Dropdown top menu | Collapses child menu items behind one trigger and shows active child label. |
| [ ] | `packages/base/src/chrome/TopMenuRail.tsx` | Secondary rail menu | Vertical subnav for addon menus with `display: "rail"`; supports optional icons and active rows. |
| [ ] | `packages/base/src/chrome/Breadcrumb.tsx` | Breadcrumb row | Shows route/page hierarchy and trailing slot for page meta or chatter toggle. |
| [ ] | `packages/base/src/chrome/GlobalSearch.tsx` | Global search button | Topbar search pill with keyboard hint and Spotlight open callback. P1 shell currently passes no open handler, so it is mostly an affordance unless wired. |
| [ ] | `packages/base/src/chrome/Spotlight.tsx` | Command palette | Exported command palette with keyboard shortcut hook, grouped commands, command run/close behavior, Dialog, and cmdk surface. |
| [ ] | `packages/base/src/chrome/Systray.tsx` | Systray | Renders addon menu items anchored at `systray`, notifications button, and help button. |
| [ ] | `packages/base/src/chrome/UserMenu.tsx` | User dropdown | Avatar dropdown with identity header, menu rows, dividers, shortcuts, link/onClick actions, and sign-out row from shell. |
| [ ] | `packages/base/src/chrome/AppChooser.tsx` | App switcher | Exported grouped/searchable app popover with active dot, disabled future apps, badges, and platform/domain grouping. |
| [ ] | `packages/base/src/chrome/icon-registry.tsx` | Icon registry | Resolves string icon ids from addon and framework icon maps. |
| [ ] | `packages/base/src/chrome/icons.tsx` | Framework icon ids | Central icon constants used by addon manifests and actions. |
| [ ] | `packages/base/src/chrome/Glyph.tsx` | Icon wrapper | Standard glyph rendering wrapper for registered/lucide icons. |
| [ ] | `packages/base/src/chrome/use-menu-strings.ts` | Menu labels | Resolves menu i18n label keys and fallback labels. |
| [ ] | `packages/base/src/chrome/_rail-styles.ts` | Rail styles | Shared app rail styling constants/classes. |
| [ ] | `packages/base/src/chrome/app-chooser-model.ts` | App chooser model | Groups, filters, and orders app chooser entries. |
| [ ] | `packages/base/src/chrome/FolderTree.tsx` | Chrome folder tree | Shared folder tree chrome primitive used by storage-style navigation. |
| [ ] | `packages/base/src/chrome/Panels.tsx` | Chrome panels | Panel layout fragments used by chrome/sidebar surfaces. |
| [ ] | `packages/base/src/chrome/SettingsRailButton.tsx` | Settings rail button | Rail button for settings/admin access. |
| [ ] | `packages/base/src/chrome/Tabs.tsx` | Chrome tabs | Tab chrome helper used by navigation panels. |

## Chatter and Communication

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/communication/Chatter.tsx` | Chatter aside | Right aside with tabs, optional composer, hidden always-mounted count sources, resizable width, collapsed unmount behavior, tab badges, and per-tab scroll areas. |
| [ ] | `packages/base/src/communication/ChatterToggle.tsx` | Chatter toggle | Breadcrumb icon button showing open/closed state and tooltip. |
| [ ] | `packages/base/src/communication/chatter-context.tsx` | Chatter state | Owns collapsed/open state and width. Width defaults to 332, clamps 240-720, persists to `localStorage` key `angee.chatter-width` with debounce. |
| [ ] | `packages/base/src/communication/chatter-count.tsx` | Live tab badges | Context that lets hidden count sources publish `count` or `null` for chatter tab badges. |
| [ ] | `packages/base/src/communication/RevisionTimeline.tsx` | Revision list | Generic newest-first revision timeline with empty/error/loading states, version sequence, relative time, author, and snapshot snippet. |
| [ ] | `packages/base/src/communication/Chat/index.tsx` | Chat primitives | Chat header/action, user/assistant/system bubbles, composer, hints, tool call frame/fallback, reasoning frame, and context block primitives used by agent chatter. |

## Feedback, Modals, and Toasts

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/feedback/ModalsHost.tsx` | Feedback modal renderer | Bridges SDK `FeedbackProvider` to AlertDialog confirm and Dialog text prompt. Supports danger confirms, required typed confirmation, trim/required text prompt, and queued requests. |
| [ ] | `packages/base/src/feedback/Toast.tsx` | Toast provider | Local/event toasts with intents info/success/warning/error/alert/critical, durations, persistent errors, close/action controls, and optional persisted `events` subscription bridge. |

## Page, Data View, Toolbar, and Actions

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/views/DataPage.tsx` | Model page preset | Owns collection/record URL routing for `/base`, `/base/new`, `/base/$id`; maps record placement route/inline/body/drawer; passes source config into `ResourcePage`; suppresses H1 because breadcrumb owns title. |
| [ ] | `packages/base/src/views/ResourcePage.tsx` | Central page owner | Fetches collection data through persisted query when configured; keeps last collection during refetch; parses `<Views>` and `<Actions>` slots; owns one DataViewProvider; chooses active collection/form view; handles record lookup, title/status page meta, chatter context, navigator pane, drawer record placement, action clusters, page control band, toolbar pagination, filters, and view switcher. |
| [ ] | `packages/base/src/toolbars/DataToolbar.tsx` | Data toolbar | Compound toolbar with root, search, filter popover, removable chips, custom filters, group-by, date granularities, saved favorites, view switcher, pagination adapter, create button, sort/group helpers, and default data toolbar. |
| [ ] | `packages/base/src/toolbars/CollectionDataToolbar.tsx` | Collection toolbar wrapper | Collection-focused toolbar adapter for actions, filters, grouping, and pagination. |
| [ ] | `packages/base/src/ui/pagination.tsx` | Pagination primitive | Page range text, page-size selector, previous/next/first/last controls, disabled states, and total-aware navigation. Must preserve page size selector. |
| [ ] | `packages/base/src/views/ListView.tsx` | Table/list view | Derives columns from `<Column>` and field metadata, supports server/client filtering, search, custom filters, group stack, sorting, pagination, selection, bulk action bar, column visibility chooser, row navigation, nested/collapsible groups, backend group counts/aggregates, aggregate footer row, skeleton/empty/error overlays, and widget cells. |
| [ ] | `packages/base/src/views/data-view-model.ts` | Client data model | Filtering, custom filters, grouping, pagination, group collapse keys/signature, selection scope reset, and bulk selection helpers. |
| [ ] | `packages/base/src/views/data-view-table.ts` | Table query mode | Decides server-backed vs client-backed table behavior and how fetched rows/total are normalized into ListView. |
| [ ] | `packages/base/src/views/aggregate-buckets.tsx` | Aggregate provider bridge | Connects SDK group/aggregate hooks to ListView group rows and footer totals. |
| [ ] | `packages/base/src/views/BoardView.tsx` | Kanban board | DnD board using `@dnd-kit`, sortable cards, droppable columns, keyboard/pointer sensors, drag overlay, grouping, row filters, row clicks, selection checkbox, card fields, skeletons, and `onCardMove`. |
| [ ] | `packages/base/src/views/board-view-model.ts` | Board move model | Pure helpers for grouping cards and applying board moves. |
| [ ] | `packages/base/src/views/CalendarView.tsx` | Calendar view | Month grid with today/prev/next, 7x6 weeks, events grouped by ISO date, CountBadge chips, and `+N more` overflow. |
| [ ] | `packages/base/src/views/GalleryView.tsx` | Gallery view | Image card grid with square thumbnail/fallback initial, title/subtitle, optional selection checkbox, keyboard activation, and custom card render. |
| [ ] | `packages/base/src/views/GanttView.tsx` | Gantt view | Horizontal timeline table with visible month window, one row per record, clipped start/end bars, tones, and row click. |
| [ ] | `packages/base/src/views/TimelineView.tsx` | Timeline view | Vertical timeline grouped by day latest-first, sticky day headings, avatar initials, actor/time/content, custom render, and click. |
| [ ] | `packages/base/src/views/TreeView.tsx` | Tree view | Recursive tree rows with parent/key/label/badge, select, custom row render, and DnD drop seam through `lib/dnd`. |
| [ ] | `packages/base/src/views/GraphView.tsx` | Graph view shell | Composes graph canvas, introspection list, inspector, toolbar/list/canvas/inspector overrides, nodes, and edges using `@xyflow/react` types. |
| [ ] | `packages/base/src/views/DashboardView.tsx` | Dashboard view | Collects `<Metric>` children into `MetricGrid` and renders remaining children stacked. |
| [ ] | `packages/base/src/views/RecordFrame.tsx` | Record drawer/frame | Drawer/body frame used by ResourcePage for record presentation and close behavior. |
| [ ] | `packages/base/src/views/useResourceAction.ts` | Resource actions | Hooks and helpers for persisted mutations, confirms, bulk actions, invalidation, toasts, navigation after action, and disabled/pending state. |
| [ ] | `packages/base/src/lib/defineView.ts` | View declaration | Adds `kind` and label metadata to React view components used by `<Views>`. |
| [ ] | `packages/base/src/views/page/Views.tsx` | Views element DSL | Declares multiple collection/record view children for ResourcePage. |
| [ ] | `packages/base/src/views/page/Actions.tsx` | Actions element DSL | Declares page/form action children for ResourcePage/FormView. |
| [ ] | `packages/base/src/views/page/Action.tsx` | Action element | Declares action id, label, icon, variant, surface, disabled state, and click handler. |
| [ ] | `packages/base/src/views/page/Column.tsx` | Column element | Declares ListView column field, widget, render function, aggregate, aggregate formatter, and column metadata. |
| [ ] | `packages/base/src/views/page/Section.tsx` | Section element | Declares ResourcePage/form section structure. |
| [ ] | `packages/base/src/page/Page.tsx` | Page primitive | Shared page layout root. |
| [ ] | `packages/base/src/page/PageHeader.tsx` | Page header | Header/title/action area primitive. |
| [ ] | `packages/base/src/page/PageToolbar.tsx` | Page toolbar | Toolbar band primitive. |
| [ ] | `packages/base/src/page/PageBody.tsx` | Page body | Main page content primitive. |
| [ ] | `packages/base/src/page/PageAside.tsx` | Page aside | Side panel primitive. |
| [ ] | `packages/base/src/page/PageFooter.tsx` | Page footer | Footer/status primitive. |
| [ ] | `packages/base/src/page/SplitPanes.tsx` | Split panes | Two-pane page layout helper. |
| [ ] | `packages/base/src/page/SectionNav.tsx` | Section nav | In-page section navigation primitive. |

## Form View and Form Layout DSL

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/views/FormView.tsx` | Record form view | TanStack Form record surface. Fetches seed record by persisted `read`+`id`, mutates with configured mutation, derives fields from `<Field>` and model metadata, maps enum choices by member, builds input from editable fields, resets after save, navigates newly-created records by id, freezes initial values against background refetch, registers unsaved navigation blocker, renders Discard/Save/pager/actions/view switcher chrome, supports header fields/statusbar and layout groups. |
| [ ] | `packages/base/src/views/form-layout/Field.tsx` | Field element | Declares a field name plus widget/title/readOnly/layout metadata; label/type/widget resolve from field metadata unless overridden. |
| [ ] | `packages/base/src/views/form-layout/Group.tsx` | Group element | Declares grouped form body sections with labels and column count. |
| [ ] | `packages/base/src/views/form-layout/Header.tsx` | Header element | Explicit form header slot override. |
| [ ] | `packages/base/src/views/form-layout/Title.tsx` | Title element | Declares editable/display title field behavior in form header. |
| [ ] | `packages/base/src/views/form-layout/StatusBar.tsx` | Status bar element | Declares statusbar/status ribbon placement in form header. |
| [ ] | `packages/base/src/views/form-layout/SmartButton.tsx` | Smart button | Header smart button/action element for record-level counts/navigation. |
| [ ] | `packages/base/src/views/form-layout/Notebook.tsx` | Notebook | Tabbed form section owner. |
| [ ] | `packages/base/src/views/form-layout/Tab.tsx` | Form tab | Notebook tab declaration. |
| [ ] | `packages/base/src/views/form-layout/Panel.tsx` | Form panel | Panel grouping primitive inside forms. |
| [ ] | `packages/base/src/views/form-layout/Relation.tsx` | Relation block | Embedded relation/list declaration inside a form, backed by model relation metadata. |
| [ ] | `packages/base/src/views/form-layout/context.tsx` | Form layout context | Provides field render hooks and layout state for nested form layout elements. |
| [ ] | `packages/base/src/views/form-layout/header-context.tsx` | Header context | Coordinates title/status/header field extraction. |
| [ ] | `packages/base/src/views/form-layout/index.ts` | Form layout barrel | Re-exports form DSL components. |
| [ ] | `packages/base/src/views/form-view-model.ts` | Form model helpers | Partitions form fields, identifies full-width widgets, derives labels/widgets, collects validation/display data, and owns form patch helpers. |

## Foundation Field Widgets

| Done | P1 source path | Widget ids / components | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/widgets/index.ts` | Widget registry | Registers foundation widget triplets for edit/read/cell surfaces. Must preserve ids exactly. |
| [ ] | `packages/base/src/widgets/text.tsx` | `text` | Text input/read/cell rendering. |
| [ ] | `packages/base/src/widgets/textarea.tsx` | `textarea` | Multi-line textarea edit/read/cell rendering. |
| [ ] | `packages/base/src/widgets/email.tsx` | `email` | Email input/read link behavior. |
| [ ] | `packages/base/src/widgets/url.tsx` | `url` | URL input/read external link behavior. |
| [ ] | `packages/base/src/widgets/phone.tsx` | `phone` | Phone input/read link behavior. |
| [ ] | `packages/base/src/widgets/integer.tsx` | `integer` | Integer field edit/read/cell rendering with number formatting. |
| [ ] | `packages/base/src/widgets/float.tsx` | `float` | Floating-point field edit/read/cell rendering with number formatting. |
| [ ] | `packages/base/src/widgets/json.tsx` | `json` | JSON edit/read/cell formatting. |
| [ ] | `packages/base/src/widgets/boolean.tsx` | `boolean` | Checkbox edit/read/cell rendering. |
| [ ] | `packages/base/src/widgets/booleanToggle.tsx` | `booleanToggle` | Switch/toggle edit/read rendering. |
| [ ] | `packages/base/src/widgets/date.tsx` | `date` | Date field widget using calendar/popover helpers. |
| [ ] | `packages/base/src/widgets/datetime.tsx` | `datetime` | Date-time field widget with date and time selection. |
| [ ] | `packages/base/src/widgets/_date-field.tsx` | Date internals | Shared calendar/popover/time-row logic for date/datetime widgets. |
| [ ] | `packages/base/src/widgets/selection.tsx` | `selection` | Select/combobox field for enum choices; uses small-choice select and searchable combobox behavior. |
| [ ] | `packages/base/src/widgets/markdown.tsx` | `markdown.editor`, `markdown.preview` | CodeMirror markdown editor with formatting toolbar and react-markdown preview with GFM; raw HTML escaped. |
| [ ] | `packages/base/src/widgets/markdown-codemirror.ts` | Markdown commands | CodeMirror setup, keymap, and formatting commands for bold, italic, code, lists, quote, and links. |
| [ ] | `packages/base/src/widgets/statusBadge.tsx` | `statusBadge` | Badge/chip display and edit behavior using field choice tone metadata or fallback heuristic. |
| [ ] | `packages/base/src/widgets/statusbar.tsx` | `statusbar` | Chevron/ribbon status control for ordered status choices, rendered in form headers. |
| [ ] | `packages/base/src/widgets/tagInput.tsx` | `tagInput` | Tag chip input/combobox behavior for editing tag lists. |
| [ ] | `packages/base/src/widgets/tagInput-model.ts` | Tag model | Normalizes, splits, adds, and removes tags. |
| [ ] | `packages/base/src/widgets/tagInput.tsx` | `tags` alias | The `tags` widget id aliases the tag-input widget triplet in P1. |
| [ ] | `packages/base/src/widgets/ribbon.tsx` | `ribbon` | Read-only status ribbon/token surface. |
| [ ] | `packages/base/src/widgets/progressBar.tsx` | `progressBar` | Progress/slider edit/read display. |
| [ ] | `packages/base/src/widgets/ownerCell.tsx` | `ownerCell` | Avatar plus display-name cell/read rendering. |
| [ ] | `packages/base/src/widgets/themePicker.tsx` | `themePicker` | Theme swatch/card toggle field for light/dark/system-style options. |
| [ ] | `packages/base/src/widgets/many2one.tsx` | `many2one` | Single-record relation select/combobox widget. |
| [ ] | `packages/base/src/widgets/many2many.tsx` | `many2many` | Multi-record relation select/combobox widget. |
| [ ] | `packages/base/src/ui/widget-control.ts` | Control chrome | Standard field control surface styling for widgets. |

## UI Primitives, Compatibility Primitives, and Fragments

These are all exported through `@angee/base` and are reachable from P1. The lift
should keep primitive behavior boring and reusable; do not bury app-specific
behavior in these files.

| Done | P1 source path | Components | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/ui/accordion.tsx` | Accordion | Collapsible grouped disclosure primitive. |
| [ ] | `packages/base/src/ui/collapsible.tsx` | Collapsible | Low-level open/closed content primitive. |
| [ ] | `packages/base/src/ui/toolbar.tsx` | Toolbar | ARIA toolbar structure used by DataToolbar/PageToolbar surfaces. |
| [ ] | `packages/base/src/ui/tree.tsx` | Tree/FolderTree | Tree item, folder tree, nested row, and selection primitives. |
| [ ] | `packages/base/src/ui/preview-frame.tsx` | PreviewFrame | Framed preview surface used by file/record previews. |
| [ ] | `packages/base/src/ui/text-link.tsx` | TextLink | Inline text link styling. |
| [ ] | `packages/base/src/ui/nav-link.tsx` | NavLink | Navigation link with active state styling. |
| [ ] | `packages/base/src/ui/chip.tsx` | Chip | Compact removable/label chip primitive. |
| [ ] | `packages/base/src/ui/section-eyebrow.tsx` | SectionEyebrow | Small section label primitive. |
| [ ] | `packages/base/src/ui/field.tsx` | Field chrome | Label/help/error field wrapper. |
| [ ] | `packages/base/src/ui/form.tsx` | Form primitives | Form layout helpers. |
| [ ] | `packages/base/src/ui/label.tsx` | Label | Field label primitive. |
| [ ] | `packages/base/src/ui/radio-group.tsx` | RadioGroup | Radio option group primitive. |
| [ ] | `packages/base/src/ui/slider.tsx` | Slider | Numeric slider primitive. |
| [ ] | `packages/base/src/ui/number-field.tsx` | NumberField | Number input primitive. |
| [ ] | `packages/base/src/ui/calendar.tsx` | Calendar | Date picker calendar primitive. |
| [ ] | `packages/base/src/ui/form-layout.tsx` | FormGrid/FormActions/FormFooter/FormSectionKicker/FieldRow | Structured form layout primitives. |
| [ ] | `packages/base/src/ui/alert.tsx` | Alert/Banner | Alert and banner messaging primitives. |
| [ ] | `packages/base/src/ui/alert-dialog.tsx` | AlertDialog | Destructive/confirm modal primitive. |
| [ ] | `packages/base/src/ui/dialog.tsx` | Dialog | General modal primitive. |
| [ ] | `packages/base/src/ui/drawer.tsx` | Drawer/Sheet | Slide-over sheet/drawer primitive. |
| [ ] | `packages/base/src/ui/popover.tsx` | Popover | Anchored floating content primitive. |
| [ ] | `packages/base/src/ui/dropdown-menu.tsx` | DropdownMenu | Menu/listbox popover primitive. |
| [ ] | `packages/base/src/ui/context-menu.tsx` | ContextMenu | Right-click context menu primitive. |
| [ ] | `packages/base/src/ui/select.tsx` | Select | Select/dropdown field primitive. |
| [ ] | `packages/base/src/ui/tabs.tsx` | Tabs | Tab list/panel primitive. |
| [ ] | `packages/base/src/ui/toggle.tsx` | Toggle | Toggle button primitive. |
| [ ] | `packages/base/src/ui/toggle-group.tsx` | ToggleGroup/SegmentedControl | Segmented controls used by view switcher, granularities, and theme picker. |
| [ ] | `packages/base/src/ui/switch.tsx` | Switch | Boolean switch primitive. |
| [ ] | `packages/base/src/ui/tooltip.tsx` | Tooltip | Hover/focus tooltip primitive. |
| [ ] | `packages/base/src/ui/skeleton.tsx` | Skeleton | Loading placeholder primitive. |
| [ ] | `packages/base/src/ui/spinner.tsx` | Spinner | Loading spinner primitive. |
| [ ] | `packages/base/src/ui/scroll-area.tsx` | ScrollArea | Scroll container primitive. |
| [ ] | `packages/base/src/ui/command.tsx` | Command | cmdk command palette primitives. |
| [ ] | `packages/base/src/ui/navigation-menu.tsx` | NavigationMenu | Top navigation menu primitive. |
| [ ] | `packages/base/src/ui/table.tsx` | Table | Table markup/styling primitives used by ListView. |
| [ ] | `packages/base/src/ui/selection-bar.tsx` | SelectionBar | Bulk selection action bar. |
| [ ] | `packages/base/src/ui/avatar.tsx` | Avatar/AvatarStack | Avatar and grouped avatar display. |
| [ ] | `packages/base/src/ui/kinded-avatar.tsx` | KindedAvatar | Avatar with semantic kind glyph/color. |
| [ ] | `packages/base/src/ui/status-icon.tsx` | StatusIcon | Status glyph/icon primitive. |
| [ ] | `packages/base/src/ui/code.tsx` | Code/CodeBlock | Inline and block code display. |
| [ ] | `packages/base/src/ui/kbd.tsx` | Kbd | Keyboard key hint primitive. |
| [ ] | `packages/base/src/ui/badge.tsx` | Badge/Tag/CountBadge | Badges, tags, and count pills. |
| [ ] | `packages/base/src/ui/button.tsx` | Button | Button variants, sizes, icon button behavior. |
| [ ] | `packages/base/src/ui/input.tsx` | Input/SearchInput/TextInput | Text input primitives. |
| [ ] | `packages/base/src/ui/textarea.tsx` | Textarea | Multiline input primitive. |
| [ ] | `packages/base/src/ui/checkbox.tsx` | Checkbox | Checkbox primitive. |
| [ ] | `packages/base/src/ui/separator.tsx` | Separator/Divider | Visual separator primitive. |
| [ ] | `packages/base/src/primitives/Avatar.tsx` | Avatar compat | Legacy/compat wrapper over avatar primitives. |
| [ ] | `packages/base/src/primitives/Button.tsx` | Button compat | Legacy/compat wrapper over button primitives. |
| [ ] | `packages/base/src/primitives/Card.tsx` | Card compat | Card surface wrapper. |
| [ ] | `packages/base/src/primitives/Checkbox.tsx` | Checkbox compat | Legacy checkbox wrapper. |
| [ ] | `packages/base/src/primitives/Combobox.tsx` | Combobox | Searchable popover select primitive. |
| [ ] | `packages/base/src/primitives/ComboboxField.tsx` | ComboboxField | Field-integrated combobox wrapper. |
| [ ] | `packages/base/src/primitives/Dot.tsx` | Dot/StatusDot | Status dot primitive. |
| [ ] | `packages/base/src/primitives/Dropzone.tsx` | Dropzone/DropOverlay | File drag/drop surface and overlay. |
| [ ] | `packages/base/src/primitives/FileIcon.tsx` | FileIcon | File type icon primitive. |
| [ ] | `packages/base/src/primitives/Kbd.tsx` | Kbd compat | Keyboard key wrapper. |
| [ ] | `packages/base/src/primitives/Popover.tsx` | Popover compat | Legacy popover wrapper. |
| [ ] | `packages/base/src/primitives/Progress.tsx` | Progress | Progress bar primitive. |
| [ ] | `packages/base/src/primitives/SearchInput.tsx` | SearchInput compat | Search input wrapper. |
| [ ] | `packages/base/src/primitives/Select.tsx` | Select compat | Legacy select wrapper. |
| [ ] | `packages/base/src/primitives/Tag.tsx` | Tag compat | Tag/chip wrapper. |
| [ ] | `packages/base/src/primitives/TextInput.tsx` | TextInput compat | Text input wrapper. |
| [ ] | `packages/base/src/fragments/RecordHeader.tsx` | RecordHeader | Reusable record header/title/status/action fragment. |
| [ ] | `packages/base/src/fragments/CollectionHeader.tsx` | CollectionHeader | Collection title/action/meta header fragment. |
| [ ] | `packages/base/src/fragments/InfoRow.tsx` | InfoRow | Label/value metadata row. |
| [ ] | `packages/base/src/fragments/MetricStrip.tsx` | MetricStrip | Horizontal metric summary strip. |
| [ ] | `packages/base/src/fragments/MetaGrid.tsx` | MetaGrid | Grid of metadata facts. |
| [ ] | `packages/base/src/fragments/HeroPanel.tsx` | HeroPanel | Auth/marketing hero panel fragment. |
| [ ] | `packages/base/src/fragments/MarketingHero.tsx` | MarketingHero | Marketing-oriented hero fragment. |
| [ ] | `packages/base/src/fragments/BrandButton.tsx` | BrandButton | Branded CTA/button fragment. |
| [ ] | `packages/base/src/fragments/FocusPanel.tsx` | FocusPanel | Focused panel layout fragment. |
| [ ] | `packages/base/src/fragments/ListPanel.tsx` | ListPanel | List-in-panel fragment. |
| [ ] | `packages/base/src/fragments/RailPanel.tsx` | RailPanel | Rail/sidebar panel fragment. |
| [ ] | `packages/base/src/fragments/SectionTabs.tsx` | SectionTabs | Section tab navigation fragment. |
| [ ] | `packages/base/src/fragments/FrameToolbar.tsx` | FrameToolbar | Toolbar frame fragment. |
| [ ] | `packages/base/src/fragments/GraphCanvas.tsx` | GraphCanvas | Graph canvas wrapper for GraphView. |
| [ ] | `packages/base/src/fragments/GraphIntrospection.tsx` | IntrospectionShell/List/Inspector | Graph list/inspector side panels. |
| [ ] | `packages/base/src/fragments/EditorHeaderBar.tsx` | EditorHeaderBar | Editor header/action bar fragment. |
| [ ] | `packages/base/src/fragments/DialogForm.tsx` | DialogForm | Dialog-hosted form fragment. |
| [ ] | `packages/base/src/fragments/EmptyState.tsx` | EmptyState | Empty/unauthorized/not-found state fragment. |
| [ ] | `packages/base/src/fragments/DataLens.tsx` | DataLens | Compact data visualization/lens fragment. |
| [ ] | `packages/base/src/fragments/DirtyPill.tsx` | DirtyPill | Unsaved/dirty state pill. |
| [ ] | `packages/base/src/fragments/LogPanel.tsx` | LogPanel | Log output panel. |
| [ ] | `packages/base/src/fragments/JsonViewer.tsx` | JsonViewer | JSON display fragment. |
| [ ] | `packages/base/src/fragments/LoadingPanel.tsx` | LoadingPanel | Loading state panel. |
| [ ] | `packages/base/src/fragments/ErrorBanner.tsx` | ErrorBanner | Error message banner. |
| [ ] | `packages/base/src/fragments/InlineEmpty.tsx` | InlineEmpty | Inline empty state. |
| [ ] | `packages/base/src/fragments/MetadataPanel.tsx` | MetadataPanel | Metadata side/detail panel. |
| [ ] | `packages/base/src/fragments/SurfaceHeader.tsx` | SurfaceHeader | Header for cards/panels/surfaces. |
| [ ] | `packages/base/src/fragments/SurfacePanel.tsx` | SurfacePanel | Generic surface panel. |
| [ ] | `packages/base/src/fragments/MiniCard.tsx` | MiniCard | Compact card fragment. |
| [ ] | `packages/base/src/fragments/MetricGrid.tsx` | MetricGrid | Dashboard metric grid. |
| [ ] | `packages/base/src/cells/TableLinkCell.tsx` | TableLinkCell | Link cell used by table/list views. |
| [ ] | `packages/base/src/cells/RecordNameLink.tsx` | RecordNameLink | Record-name link cell. |
| [ ] | `packages/base/src/cells/MonoCell.tsx` | MonoCell | Monospace cell formatter. |
| [ ] | `packages/base/src/cells/StatusPill.tsx` | StatusPill | Status badge/pill table cell. |
| [ ] | `packages/base/src/cells/EmptyCell.tsx` | EmptyCell | Placeholder for empty table values. |

## Layouts, Uploads, Preview, and Utilities

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `packages/base/src/layouts/RecordView.tsx` | Record layout | Record-detail layout helper. |
| [ ] | `packages/base/src/layouts/SplitView.tsx` | Split layout | Split master/detail layout helper. |
| [ ] | `packages/base/src/layouts/HeroPage.tsx` | Hero layout | Hero-style page layout helper. |
| [ ] | `packages/base/src/layouts/CanvasPage.tsx` | Canvas layout | Full-canvas page layout helper. |
| [ ] | `packages/base/src/layouts/extract.ts` | Layout extraction | Helpers for parsing layout children/slots. |
| [ ] | `packages/base/src/upload/queue.tsx` | Upload queue | Provides upload task queue/context used by storage file upload surfaces. |
| [ ] | `packages/base/src/upload/index.ts` | Upload exports | Upload hooks/types/helpers barrel. |
| [ ] | `packages/base/src/upload/client.ts` | Upload client | Client helper for posting upload requests. |
| [ ] | `packages/base/src/upload/types.ts` | Upload types | Upload task state, metadata, and status types. |
| [ ] | `packages/base/src/preview/registry.ts` | Preview registry | Registers preview renderers by file/content type. |
| [ ] | `packages/base/src/preview/config.ts` | Preview config | Default preview configuration and lookup behavior. |
| [ ] | `packages/base/src/preview/PreviewPane.tsx` | PreviewPane | Runtime preview pane that resolves a renderer for a file/resource. |
| [ ] | `packages/base/src/lib/dnd.ts` | DnD helpers | Shared drag/drop payload and guard helpers used by TreeView and board surfaces. |
| [ ] | `packages/base/src/lib/time.ts` | Time helpers | Relative/date formatting helpers used by timeline/chatter/widgets. |
| [ ] | `packages/base/src/lib/labelCase.ts` | String helpers | Label/title/case helper used by views/widgets. |
| [ ] | `packages/base/src/lib/query-state/index.ts` | Query-state helpers | URL/query-state helper surface for filters and route links. |
| [ ] | `packages/base/src/styles/tokens.css` | Theme tokens | CSS token source consumed by base components and theme-aware widgets. |
| [ ] | `packages/base-previews/src/index.ts` | Preview renderers barrel | Registers default file preview renderers. |
| [ ] | `packages/base-previews/src/*` | Preview renderer files | PDF/image/text/markdown/json/video/audio/etc. preview renderers reachable through storage preview configuration. |

## Notes Example Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/index.ts` | Notes addon manifest | Defines `notes` addon id, `/notes` routes, rail menu with tabs, widgets, notes i18n, and chatter override for `activity` with live count source. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/NotePage.tsx` | Notes page | Composes `DataPage` for model `note`; declares status filters, updated/status group options, persisted query `notes`, result field `notes`, row/status labels, list/board/form views, New note action, archive/delete/share record actions, and archive/delete bulk actions with confirms/toasts. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/views/NoteListView.tsx` | Notes list view | `defineView(kind: "list")`; uses `ListView`, row links to `/notes/:id`, columns title/tags/status/wordCount/updatedAt, tag chips limited to three, `wordCount` sum aggregate with formatter, and custom updated-at widget. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/views/NoteFormView.tsx` | Notes form view | `defineView(kind: "form")`; uses `FormView`, header title and statusbar, Details group with tags, word counter, created/updated by/at read-only fields, and Body group with markdown editor. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/views/note-status.tsx` | Notes view model | Declares `NoteRow`, status order `DRAFT`, `IN_REVIEW`, `ACTIVE`, `ARCHIVED`, status label formatting, and backend aggregate word-count formatter. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/NoteRevisionsTab.tsx` | Notes activity chatter | Fetches `noteRevisions(id)` for open record and renders `RevisionTimeline`; hidden `NoteRevisionsCount` publishes live Activity badge count or `null` when unavailable. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/widgets/word-counter.tsx` | `notes.wordCounter` | Reads form body from `useFormState`, counts live words, falls back to persisted value, renders read-only control surface with pluralized count. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/widgets/note-word-count.ts` | Word count model | Pure word counting and formatting helper for note bodies. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/widgets/updated-at.ts` | Updated-at formatter | Formats timestamps into relative/calendar labels for note list cells. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/widgets/updated-at-widget.tsx` | `notes.updatedAt` | Read/edit/cell widget triplet that renders formatted updated-at text. |
| [ ] | `examples/angee-notes/addons/notes/ui/base/src/widgets/index.ts` | Notes widgets barrel | Exports notes widget ids, triplets, components, and helpers. |

## Auth Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/auth/ui/base/src/index.ts` | Auth addon manifest | Exports `AUTH_LOGIN_SLOT`; adds public `/login`; adds admin console routes for overview, users, groups, machines, roles, grants, schema, and relationships; contributes Auth rail menu with tab display and i18n. |
| [ ] | `addons/auth/ui/base/src/contracts.ts` | Auth contracts | Login slot ids, role constants, and auth-related UI contracts. |
| [ ] | `addons/auth/ui/base/src/views/LoginPage.tsx` | Login page | Public login route that composes auth hero, username/password form, login-method slots, safe next-path handling, and public shell visuals. |
| [ ] | `addons/auth/ui/base/src/fragments/UsernamePasswordForm.tsx` | Credentials form | Username/password login form backed by persisted login mutation and auth state refresh. |
| [ ] | `addons/auth/ui/base/src/fragments/HeroPanel.tsx` | Login hero | Auth-branded hero content used by login page. |
| [ ] | `addons/auth/ui/base/src/fragments/login-methods.tsx` | Login method slots | Renders method-region slot contributions such as OAuth buttons. |
| [ ] | `addons/auth/ui/base/src/fragments/PrincipalAvatar.tsx` | Principal avatar | Avatar display for users, groups, machines, or principals. |
| [ ] | `addons/auth/ui/base/src/fragments/GrantComposer.tsx` | Grant composer | UI to compose/create permission grants. |
| [ ] | `addons/auth/ui/base/src/fragments/RevokeGrantButton.tsx` | Revoke grant | Button/action for revoking grants with mutation behavior. |
| [ ] | `addons/auth/ui/base/src/views/OverviewPage.tsx` | Auth overview | Admin overview/metrics entry screen for identity and permissions. |
| [ ] | `addons/auth/ui/base/src/views/PrincipalsPage.tsx` | Principals page | Route wrapper for users/groups/machines principal sections. |
| [ ] | `addons/auth/ui/base/src/views/PrincipalListView.tsx` | Principal list | List view for user/group/machine principals. |
| [ ] | `addons/auth/ui/base/src/views/PrincipalFormView.tsx` | Principal form | Record form for principal details. |
| [ ] | `addons/auth/ui/base/src/views/RolesPage.tsx` | Roles page | Role management route/page. |
| [ ] | `addons/auth/ui/base/src/views/RoleListView.tsx` | Roles list | List/table of auth roles. |
| [ ] | `addons/auth/ui/base/src/views/GrantsPage.tsx` | Grants page | Grant management route/page. |
| [ ] | `addons/auth/ui/base/src/views/GrantListView.tsx` | Grants list | List/table of grants with revoke actions. |
| [ ] | `addons/auth/ui/base/src/views/RelationshipsPage.tsx` | Relationships page | ReBAC relationship route/page. |
| [ ] | `addons/auth/ui/base/src/views/RelationshipListView.tsx` | Relationships list | List/table for relationship tuples. |
| [ ] | `addons/auth/ui/base/src/views/SchemaPage.tsx` | Schema page | ReBAC/permission schema display. |
| [ ] | `addons/auth/ui/base/src/views/row-adapters.ts` | Row adapters | Normalizes backend rows into UI list/form row shapes. |
| [ ] | `addons/auth/ui/base/src/views/metrics.ts` | Metrics helpers | Computes auth overview metric values. |
| [ ] | `addons/auth/ui/base/src/views/rebac-labels.ts` | ReBAC labels | User-facing labels for relationship/schema facts. |
| [ ] | `addons/auth/ui/base/src/views/enum-tone.ts` | Enum tone helpers | Tone mapping for auth enum/status cells. |
| [ ] | `addons/auth/ui/base/src/safe-next-path.ts` | Safe redirects | Validates login `next` redirects to local safe paths. |
| [ ] | `addons/auth/ui/base/src/i18n.ts` | Auth i18n | Auth namespace translations for routes/menus/views. |

## Integrate Auth OAuth Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/index.ts` | Integrate addon manifest | Adds OAuth login method slot, public `/sso/callback`, admin routes for Integrate overview/vendors/oauth/accounts/providers/bridges/webhooks, rail menu display, admin roles, and i18n. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/auth/OidcCallbackPage.tsx` | OIDC callback | Public callback page for SSO/OIDC login completion. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/auth/OidcLoginButtonRow.tsx` | Login slot component | Renders OIDC provider buttons in auth login method slot. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/auth/OidcProviderButton.tsx` | Provider button | Single OAuth/OIDC provider login button. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/auth/ProviderIcon.tsx` | Provider icon | Provider icon/glyph selection. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/CrudSection.tsx` | CRUD section helper | Shared CRUD section wrapper for Integrate admin resources. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/IntegrateFormView.tsx` | Integrate form | Shared form view for Integrate resources. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/OverviewPage.tsx` | Overview | Integrate admin overview. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/VendorsPage.tsx` | Vendors | Vendor list/detail CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/OAuthProvidersPage.tsx` | OAuth providers | OAuth provider CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/AccountsPage.tsx` | Accounts | OAuth account CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/CapabilityProvidersPage.tsx` | Capability providers | Provider capability CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/CapabilityBridgesPage.tsx` | Capability bridges | Bridge CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/WebhooksPage.tsx` | Webhooks | Webhook CRUD route. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/views/integrate-schema.ts` | Schema config | Field/view schema declarations for Integrate resources. |
| [ ] | `addons/integrate-auth-oauth/ui/base/src/i18n.ts` | Integrate i18n | Integrate namespace translations. |

## Storage Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/storage/ui/base/src/index.ts` | Storage addon manifest | Calls preview configuration side effects, adds `/storage`, `/storage/$id`, and `/storage/attachments`; contributes Files rail menu, storage widgets, i18n, file badge/tab slot ids, and upload/preview exports. |
| [ ] | `addons/storage/ui/base/src/views/StoragePage.tsx` | Storage page | Main file manager route wrapper. |
| [ ] | `addons/storage/ui/base/src/views/FileListView.tsx` | File list | File table/grid list with folder navigation and file actions. |
| [ ] | `addons/storage/ui/base/src/views/FileDetailView.tsx` | File detail | File record/detail view including metadata and preview. |
| [ ] | `addons/storage/ui/base/src/views/FilePreview.tsx` | File preview | Preview pane for selected file using preview registry. |
| [ ] | `addons/storage/ui/base/src/views/FolderTree.tsx` | Folder tree | Drive/folder navigation tree. |
| [ ] | `addons/storage/ui/base/src/views/DriveSwitcher.tsx` | Drive switcher | Drive selection control. |
| [ ] | `addons/storage/ui/base/src/views/UploadTray.tsx` | Upload tray | Upload queue/status tray for active and completed uploads. |
| [ ] | `addons/storage/ui/base/src/views/FolderDialog.tsx` | Folder dialog | Create/rename/move folder dialog surface. |
| [ ] | `addons/storage/ui/base/src/views/AttachmentsView.tsx` | Attachments | Attachment management route/list. |
| [ ] | `addons/storage/ui/base/src/views/FileIconLink.tsx` | File icon link | Link cell/row with file icon and file name. |
| [ ] | `addons/storage/ui/base/src/views/file-manager-model.ts` | File manager model | Pure helpers for folder/file navigation and state. |
| [ ] | `addons/storage/ui/base/src/views/file-schema.ts` | File schema | File/attachment schema and column/field declarations. |
| [ ] | `addons/storage/ui/base/src/widgets/fileBrowser.tsx` | `storage.fileBrowser` | File browser picker widget. |
| [ ] | `addons/storage/ui/base/src/widgets/fileIcon.tsx` | `storage.fileIcon` | File icon widget based on type/name. |
| [ ] | `addons/storage/ui/base/src/widgets/filePreview.tsx` | `storage.filePreview` | File preview widget. |
| [ ] | `addons/storage/ui/base/src/widgets/fileUpload.tsx` | `storage.fileUpload` | Dropzone/upload widget integrated with upload queue/task list. |
| [ ] | `addons/storage/ui/base/src/widgets/index.ts` | Storage widgets barrel | Exports storage widget triplets and ids. |
| [ ] | `addons/storage/ui/base/src/i18n.ts` | Storage i18n | Storage namespace translations. |

## Agents Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/agents/ui/base/src/index.ts` | Agents addon manifest | Adds Agents, Models, Vendors, and Availability admin routes; contributes rail menu; contributes always-scoped `angee` AI chatter tab; registers `agents.acpSessions` widget; exports ACP runtime/glue and i18n. |
| [ ] | `addons/agents/ui/base/src/roles.ts` | Agents roles | Admin role constants for route/menu gates. |
| [ ] | `addons/agents/ui/base/src/lib/agent-acp-sessions-widget.tsx` | `agents.acpSessions` | Live ACP session inspector widget. |
| [ ] | `addons/agents/ui/base/src/chatter/AgentChatter.tsx` | Agent chatter UI | Chat surface wiring for the Angee assistant tab. |
| [ ] | `addons/agents/ui/base/src/chatter/AgentChatterTab.tsx` | Agent chatter tab | Chatter contribution component for always-scoped AI assistant. |
| [ ] | `addons/agents/ui/base/src/acp/*` | ACP runtime/glue | Client/runtime helpers for agent protocol interactions. |
| [ ] | `addons/agents/ui/base/src/views/AgentsPage.tsx` | Agents page | Agents collection/record route wrapper. |
| [ ] | `addons/agents/ui/base/src/views/AgentDetailView.tsx` | Agent detail | Agent record detail/form view. |
| [ ] | `addons/agents/ui/base/src/views/AgentSetupView.tsx` | Agent setup | New/setup route view for agents. |
| [ ] | `addons/agents/ui/base/src/views/InferenceModelsPage.tsx` | Inference models | Model collection/detail route wrapper. |
| [ ] | `addons/agents/ui/base/src/views/InferenceModelFormView.tsx` | Model form | Inference model record form. |
| [ ] | `addons/agents/ui/base/src/views/InferenceVendorsPage.tsx` | Inference vendors | Vendor collection/detail route wrapper. |
| [ ] | `addons/agents/ui/base/src/views/InferenceVendorFormView.tsx` | Vendor form | Inference vendor record form. |
| [ ] | `addons/agents/ui/base/src/views/AvailabilityPage.tsx` | Availability | Agent/model availability status page. |
| [ ] | `addons/agents/ui/base/src/views/schema.ts` | Agents schema | Field/column/view declarations for agents resources. |
| [ ] | `addons/agents/ui/base/src/i18n.ts` | Agents i18n | Agents namespace translations. |

## Angee Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/angee/ui/base/src/index.ts` | Angee addon manifest | Adds admin routes for graph, models, model detail, fields, addons, addon detail, and assets; contributes Angee rail menu with custom `angee.brand` icon; exports pages and i18n. |
| [ ] | `addons/angee/ui/base/src/AngeeLogo.tsx` | Angee logo | Custom brand icon registered by addon manifest. |
| [ ] | `addons/angee/ui/base/src/views/GraphPage.tsx` | Graph page | System graph/explorer route. |
| [ ] | `addons/angee/ui/base/src/views/ModelsPage.tsx` | Models page | Model catalog/list route. |
| [ ] | `addons/angee/ui/base/src/views/ModelDetailView.tsx` | Model detail | Model detail with relations/smart buttons. |
| [ ] | `addons/angee/ui/base/src/views/FieldsPage.tsx` | Fields page | Field catalog/list route. |
| [ ] | `addons/angee/ui/base/src/views/AddonsPage.tsx` | Addons page | Addon catalog/list route. |
| [ ] | `addons/angee/ui/base/src/views/AddonDetailView.tsx` | Addon detail | Addon detail with relation sections. |
| [ ] | `addons/angee/ui/base/src/views/AssetsPage.tsx` | Assets page | Asset catalog/list route. |
| [ ] | `addons/angee/ui/base/src/views/explorer.tsx` | Explorer helpers | Model/system explorer UI helpers. |
| [ ] | `addons/angee/ui/base/src/views/model-graph.tsx` | Model graph | Graph node/edge helpers for model relationships. |
| [ ] | `addons/angee/ui/base/src/views/data-view-utils.ts` | Data view utilities | Shared Angee addon table/view helpers. |
| [ ] | `addons/angee/ui/base/src/views/record-paths.ts` | Record paths | Route/path builders for system records. |
| [ ] | `addons/angee/ui/base/src/views/system-rows.ts` | System rows | Row adapters for system metadata records. |
| [ ] | `addons/angee/ui/base/src/i18n.ts` | Angee i18n | Angee namespace translations. |

## Knowledge Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/knowledge/ui/base/src/index.ts` | Knowledge addon manifest | Adds Knowledge routes for pages, vaults, canvases, databases, graph, and imports; contributes Knowledge rail menu and i18n. |
| [ ] | `addons/knowledge/ui/base/src/views/sections.tsx` | Knowledge sections | Section placeholders/content fragments for Knowledge routes. |
| [ ] | `addons/knowledge/ui/base/src/i18n.ts` | Knowledge i18n | Knowledge namespace translations. |

## Operator Addon

| Done | P1 source path | Surface | Current behavior |
| --- | --- | --- | --- |
| [ ] | `addons/operator/ui/base/src/index.ts` | Operator addon manifest | Adds admin routes for overview, services, workspaces, sources, GitOps, operations, templates, and secrets; contributes Operator rail menu placement end; exports daemon docs/helpers and i18n. |
| [ ] | `addons/operator/ui/base/src/roles.ts` | Operator roles | Admin role constants for gates. |
| [ ] | `addons/operator/ui/base/src/views/OperatorSectionFrame.tsx` | Section frame | Shared frame for Operator sections. |
| [ ] | `addons/operator/ui/base/src/views/pages.tsx` | Operator pages | Route/page composition for all operator sections. |
| [ ] | `addons/operator/ui/base/src/views/sections/OverviewSection.tsx` | Overview section | Operator overview section and metrics. |
| [ ] | `addons/operator/ui/base/src/views/sections/ServicesSection.tsx` | Services section | Services list/actions section. |
| [ ] | `addons/operator/ui/base/src/views/sections/WorkspacesSection.tsx` | Workspaces section | Workspaces list/actions section. |
| [ ] | `addons/operator/ui/base/src/views/sections/SourcesSection.tsx` | Sources section | Sources/repositories section. |
| [ ] | `addons/operator/ui/base/src/views/sections/GitOpsSection.tsx` | GitOps section | GitOps state/actions section. |
| [ ] | `addons/operator/ui/base/src/views/sections/OperationsSection.tsx` | Operations section | Operations/job history section. |
| [ ] | `addons/operator/ui/base/src/views/sections/TemplatesSection.tsx` | Templates section | Template catalog section. |
| [ ] | `addons/operator/ui/base/src/views/sections/SecretsSection.tsx` | Secrets section | Secrets management section. |
| [ ] | `addons/operator/ui/base/src/views/panels/WorkspaceCreatePanel.tsx` | Workspace create | Create-workspace panel/form. |
| [ ] | `addons/operator/ui/base/src/views/panels/ServiceCreatePanel.tsx` | Service create | Create-service panel/form. |
| [ ] | `addons/operator/ui/base/src/views/panels/StackLogsPanel.tsx` | Stack logs | Stack/service log panel. |
| [ ] | `addons/operator/ui/base/src/views/StateTag.tsx` | State tag | Status tag for operator resource states. |
| [ ] | `addons/operator/ui/base/src/views/data/client.ts` | Daemon client | Operator daemon GraphQL/urql client setup. |
| [ ] | `addons/operator/ui/base/src/views/data/hooks.ts` | Daemon hooks | Data hooks for daemon queries/mutations/subscriptions. |
| [ ] | `addons/operator/ui/base/src/views/data/list-rows.ts` | List rows | Row adapters for operator tables. |
| [ ] | `addons/operator/ui/base/src/views/data/mutations.ts` | Mutations | Operator mutation helpers. |
| [ ] | `addons/operator/ui/base/src/views/data/snapshot.ts` | Snapshot | Snapshot normalization for operator state. |
| [ ] | `addons/operator/ui/base/src/views/data/state-buckets.ts` | State buckets | State grouping/count helpers. |
| [ ] | `addons/operator/ui/base/src/views/data/template-input-fields.ts` | Template fields | Dynamic template input field definitions. |
| [ ] | `addons/operator/ui/base/src/views/data/overview-metrics.ts` | Overview metrics | Metric computations for overview. |
| [ ] | `addons/operator/ui/base/src/views/data/create-requests.ts` | Create requests | Request builders for create panels. |
| [ ] | `addons/operator/ui/base/src/views/data/provisioning-docs.ts` | Provisioning docs | Operator provisioning help/docs data. |
| [ ] | `addons/operator/ui/base/src/i18n.ts` | Operator i18n | Operator namespace translations. |

## Cross-Cutting Details That Must Not Be Missed

- [ ] App route names and paths: `/notes`, `/notes/$id`, `/notes/new`,
      `/login`, `/sso/callback`, `/auth/*`, `/storage/*`, `/agents/*`,
      `/angee/*`, `/knowledge/*`, `/operator/*`, `/integrate/*`.
- [ ] Menu anchors: `rail`, `user`, `systray`, and addon parent ids such as
      `notes`, `auth`, `storage`, `agents`, `angee`, `knowledge`, `operator`,
      and `integrate`.
- [ ] Menu display modes: `tabs`, `dropdown`, and `rail`.
- [ ] Menu placement: `start`, `center`, `end`; Auth/Operator are end-placed in
      P1.
- [ ] Role gates: route/menu roles must preserve any-of semantics and
      unauthorized in-shell empty state.
- [ ] Login redirect: anonymous console routes redirect to `/login?next=...`;
      login must validate safe next paths.
- [ ] Login slots: `AUTH_LOGIN_SLOT`, method region for OAuth, card footer
      region for demo credentials.
- [ ] Chatter tabs: default `angee`, `comments`, `activity`; notes overrides
      `activity`; agents overrides/contributes always-scoped AI tab.
- [ ] Chatter live counts: hidden `countSource` components must mount even when
      another chatter tab is active.
- [ ] Chatter width persistence: `angee.chatter-width`.
- [ ] Data-view favorite persistence: `angee:data-view:<resource>:favorites`.
- [ ] Default page size: 50.
- [ ] Page size selector: preserved in `ui/pagination.tsx` and
      `DataToolbar.Pagination`.
- [ ] Column selector: visible from the table header/page ListView chrome; not
      hidden in a separate or unreachable toolbar.
- [ ] Filter popover columns: Filters, Group by, Favorites.
- [ ] Custom filter operators: equality, contains, comparisons, empty checks,
      `in`, `notIn`, and type-specific value editors.
- [ ] Date group granularities: day, week, month, quarter, year.
- [ ] View switcher kinds: table/list/cards/grid/board/kanban/form/detail/
      calendar/pivot/graph where supported by the base control.
- [ ] Group stack, group ordering, group collapse state, and backend aggregate
      buckets must survive the lift.
- [ ] Aggregate chain example: the UI can group/aggregate by year and then add
      month under that year without flattening or replacing the first grouping.
- [ ] Backend aggregates: `Column aggregate="sum"` for notes wordCount must use
      backend aggregate data over the full filtered/grouped set, not page-local
      rows.
- [ ] Bulk actions: selected-row archive/delete with count-sensitive confirms
      and toasts.
- [ ] Record actions: New note, Archive, Share, Delete with primary/more
      surfaces and pending/disabled states.
- [ ] Row navigation: table rows support keyboard activation and modifier-click
      link behavior.
- [ ] Column visibility chooser: cannot hide the last column.
- [ ] Selection scope reset: selection clears when filter/sort/page/group scope
      changes.
- [ ] Form dirty guard: unsaved form navigation blocker must use confirm modal.
- [ ] Save/Discard visibility: Save and Discard appear only when the form is
      dirty; they do not occupy primary chrome on a clean form.
- [ ] Save stability: saving does not flash, reload, revert to the initial
      value, or clobber user-visible state with stale fetch data.
- [ ] Form create navigation: new record save navigates to the saved record id.
- [ ] Header fields: title/statusbar render in form header and are not repeated
      in the body.
- [ ] Full-width form widgets: textarea, markdown, tag input, and any other
      widgets marked full-width by form layout helpers.
- [ ] Markdown editor toolbar: bold, italic, inline code, bullet list, numbered
      list, quote, link popover, and keybindings.
- [ ] Markdown editor layout: no duplicate field label inside the editor body,
      no cramped toolbar overlap, and no broken focus/border treatment like the
      current Body screenshot regression.
- [ ] Notes word-count override: notes form field uses `widget="notes.wordCounter"`
      so the displayed count follows the live body field through `useFormState`.
- [ ] Status choices and tones: derive from generated field metadata where
      available.
- [ ] Relation widgets: preserve `many2one` and `many2many` widget ids and
      combobox behavior.
- [ ] Upload queue: preserve provider, dropzone, task states, tray, and storage
      widget integration.
- [ ] Preview registry: storage must configure and register default preview
      renderers before file previews mount.
- [ ] App rail reorder: pointer and keyboard DnD, persisted order hook, badges,
      tooltips, and default-app long press.
- [ ] Systray and user menu: addon-contributed menu rows plus notifications,
      help, identity header, shortcuts, and sign-out.
- [ ] Toast event bridge: if the `events` operation exists, toasts subscribe to
      backend events; otherwise local toasts still work.
- [ ] Generated runtime: do not hand-edit `runtime/__generated__`; regenerate
      after source changes.

## Exact SDK Source Checklist

This is the complete P1 `packages/sdk/src` path list captured during inventory.
Each file must be lifted, replaced by an equivalent owner, or intentionally
deleted with a written rationale.

- [ ] `packages/sdk/src/aggregates.ts`
- [ ] `packages/sdk/src/contracts.ts`
- [ ] `packages/sdk/src/csrf.ts`
- [ ] `packages/sdk/src/data-view.tsx`
- [ ] `packages/sdk/src/define-addon.ts`
- [ ] `packages/sdk/src/feedback.ts`
- [ ] `packages/sdk/src/graphql-client.ts`
- [ ] `packages/sdk/src/graphql-context.ts`
- [ ] `packages/sdk/src/graphql-hooks.ts`
- [ ] `packages/sdk/src/graphql-provider.tsx`
- [ ] `packages/sdk/src/i18n.ts`
- [ ] `packages/sdk/src/index.ts`
- [ ] `packages/sdk/src/model-fields.ts`
- [ ] `packages/sdk/src/operations.ts`
- [ ] `packages/sdk/src/permissions.ts`
- [ ] `packages/sdk/src/persisted.ts`
- [ ] `packages/sdk/src/providers.tsx`
- [ ] `packages/sdk/src/routes.tsx`
- [ ] `packages/sdk/src/runtime.tsx`
- [ ] `packages/sdk/src/storybook.tsx`
- [ ] `packages/sdk/src/styles.css`

## Exact Base Source Checklist

This is the complete P1 `packages/base/src` path list captured during inventory.
The detailed sections above describe the current behavior by owner; this appendix
is the mechanical no-missing-files checklist.

### Base Root, App, Assets

- [ ] `packages/base/src/addon-routes.ts`
- [ ] `packages/base/src/app/app-shell-context.tsx`
- [ ] `packages/base/src/app/menu-tree.ts`
- [ ] `packages/base/src/assets/angee-vision-background.webp`
- [ ] `packages/base/src/createApp.tsx`
- [ ] `packages/base/src/index.ts`

### Base Cells

- [ ] `packages/base/src/cells/EmptyCell.tsx`
- [ ] `packages/base/src/cells/index.ts`
- [ ] `packages/base/src/cells/MonoCell.tsx`
- [ ] `packages/base/src/cells/RecordNameLink.tsx`
- [ ] `packages/base/src/cells/StatusPill.tsx`
- [ ] `packages/base/src/cells/TableLinkCell.tsx`

### Base Chrome

- [ ] `packages/base/src/chrome/_rail-styles.ts`
- [ ] `packages/base/src/chrome/app-chooser-model.ts`
- [ ] `packages/base/src/chrome/app-rail-model.ts`
- [ ] `packages/base/src/chrome/AppBrand.tsx`
- [ ] `packages/base/src/chrome/AppChooser.tsx`
- [ ] `packages/base/src/chrome/AppRail.tsx`
- [ ] `packages/base/src/chrome/Breadcrumb.tsx`
- [ ] `packages/base/src/chrome/FolderTree.tsx`
- [ ] `packages/base/src/chrome/GlobalSearch.tsx`
- [ ] `packages/base/src/chrome/Glyph.tsx`
- [ ] `packages/base/src/chrome/icon-registry.tsx`
- [ ] `packages/base/src/chrome/icons.tsx`
- [ ] `packages/base/src/chrome/Panels.tsx`
- [ ] `packages/base/src/chrome/SettingsRailButton.tsx`
- [ ] `packages/base/src/chrome/Spotlight.tsx`
- [ ] `packages/base/src/chrome/Systray.tsx`
- [ ] `packages/base/src/chrome/Tabs.tsx`
- [ ] `packages/base/src/chrome/TopBar.tsx`
- [ ] `packages/base/src/chrome/TopMenu.tsx`
- [ ] `packages/base/src/chrome/TopMenuDropdown.tsx`
- [ ] `packages/base/src/chrome/TopMenuRail.tsx`
- [ ] `packages/base/src/chrome/use-menu-strings.ts`
- [ ] `packages/base/src/chrome/UserMenu.tsx`

### Base Communication

- [ ] `packages/base/src/communication/Chat/index.tsx`
- [ ] `packages/base/src/communication/chatter-context.tsx`
- [ ] `packages/base/src/communication/chatter-count.tsx`
- [ ] `packages/base/src/communication/Chatter.tsx`
- [ ] `packages/base/src/communication/ChatterToggle.tsx`
- [ ] `packages/base/src/communication/RevisionTimeline.tsx`

### Base Feedback

- [ ] `packages/base/src/feedback/ModalsHost.tsx`
- [ ] `packages/base/src/feedback/Toast.tsx`

### Base Fragments

- [ ] `packages/base/src/fragments/BrandButton.tsx`
- [ ] `packages/base/src/fragments/CollectionHeader.tsx`
- [ ] `packages/base/src/fragments/DataLens.tsx`
- [ ] `packages/base/src/fragments/DialogForm.tsx`
- [ ] `packages/base/src/fragments/DirtyPill.tsx`
- [ ] `packages/base/src/fragments/EditorHeaderBar.tsx`
- [ ] `packages/base/src/fragments/EmptyState.tsx`
- [ ] `packages/base/src/fragments/ErrorBanner.tsx`
- [ ] `packages/base/src/fragments/FocusPanel.tsx`
- [ ] `packages/base/src/fragments/FrameToolbar.tsx`
- [ ] `packages/base/src/fragments/GraphCanvas.tsx`
- [ ] `packages/base/src/fragments/GraphIntrospection.tsx`
- [ ] `packages/base/src/fragments/HeroPanel.tsx`
- [ ] `packages/base/src/fragments/index.ts`
- [ ] `packages/base/src/fragments/InfoRow.tsx`
- [ ] `packages/base/src/fragments/InlineEmpty.tsx`
- [ ] `packages/base/src/fragments/JsonViewer.tsx`
- [ ] `packages/base/src/fragments/ListPanel.tsx`
- [ ] `packages/base/src/fragments/LoadingPanel.tsx`
- [ ] `packages/base/src/fragments/LogPanel.tsx`
- [ ] `packages/base/src/fragments/MarketingHero.tsx`
- [ ] `packages/base/src/fragments/MetadataPanel.tsx`
- [ ] `packages/base/src/fragments/MetaGrid.tsx`
- [ ] `packages/base/src/fragments/MetricGrid.tsx`
- [ ] `packages/base/src/fragments/MetricStrip.tsx`
- [ ] `packages/base/src/fragments/MiniCard.tsx`
- [ ] `packages/base/src/fragments/RailPanel.tsx`
- [ ] `packages/base/src/fragments/RecordHeader.tsx`
- [ ] `packages/base/src/fragments/SectionTabs.tsx`
- [ ] `packages/base/src/fragments/SurfaceHeader.tsx`
- [ ] `packages/base/src/fragments/SurfacePanel.tsx`

### Base I18n, Layouts, Page

- [ ] `packages/base/src/i18n/en.ts`
- [ ] `packages/base/src/i18n/index.ts`
- [ ] `packages/base/src/layouts/CanvasPage.tsx`
- [ ] `packages/base/src/layouts/extract.ts`
- [ ] `packages/base/src/layouts/HeroPage.tsx`
- [ ] `packages/base/src/layouts/index.ts`
- [ ] `packages/base/src/layouts/RecordView.tsx`
- [ ] `packages/base/src/layouts/SplitView.tsx`
- [ ] `packages/base/src/page/index.ts`
- [ ] `packages/base/src/page/Page.tsx`
- [ ] `packages/base/src/page/PageAside.tsx`
- [ ] `packages/base/src/page/PageBody.tsx`
- [ ] `packages/base/src/page/PageFooter.tsx`
- [ ] `packages/base/src/page/PageHeader.tsx`
- [ ] `packages/base/src/page/PageToolbar.tsx`
- [ ] `packages/base/src/page/SectionNav.tsx`
- [ ] `packages/base/src/page/SplitPanes.tsx`

### Base Lib

- [ ] `packages/base/src/lib/cn.ts`
- [ ] `packages/base/src/lib/crypto.ts`
- [ ] `packages/base/src/lib/defineView.ts`
- [ ] `packages/base/src/lib/dnd.ts`
- [ ] `packages/base/src/lib/errorMessage.ts`
- [ ] `packages/base/src/lib/file-kind.ts`
- [ ] `packages/base/src/lib/form-patch.ts`
- [ ] `packages/base/src/lib/formatDate.ts`
- [ ] `packages/base/src/lib/graphTokens.ts`
- [ ] `packages/base/src/lib/index.ts`
- [ ] `packages/base/src/lib/initials.ts`
- [ ] `packages/base/src/lib/labelCase.ts`
- [ ] `packages/base/src/lib/navigation.ts`
- [ ] `packages/base/src/lib/pluralise.ts`
- [ ] `packages/base/src/lib/query-state/index.ts`
- [ ] `packages/base/src/lib/query-state/QueryStateProvider.tsx`
- [ ] `packages/base/src/lib/query-state/types.ts`
- [ ] `packages/base/src/lib/query-state/useQueryRows.ts`
- [ ] `packages/base/src/lib/query-state/useQueryState.ts`
- [ ] `packages/base/src/lib/searchFilter.ts`
- [ ] `packages/base/src/lib/slot.ts`
- [ ] `packages/base/src/lib/slotRegistry.ts`
- [ ] `packages/base/src/lib/statusVariants.ts`
- [ ] `packages/base/src/lib/tailwind-merge-config.ts`
- [ ] `packages/base/src/lib/time.ts`
- [ ] `packages/base/src/lib/tones.ts`
- [ ] `packages/base/src/lib/useCopyState.ts`
- [ ] `packages/base/src/lib/useRouteParam.ts`
- [ ] `packages/base/src/lib/useToastRunner.ts`
- [ ] `packages/base/src/lib/variants.ts`

### Base Preview

- [ ] `packages/base/src/preview/config.ts`
- [ ] `packages/base/src/preview/index.ts`
- [ ] `packages/base/src/preview/model.ts`
- [ ] `packages/base/src/preview/PreviewPane.tsx`
- [ ] `packages/base/src/preview/registry.ts`

### Base Primitives

- [ ] `packages/base/src/primitives/Avatar.tsx`
- [ ] `packages/base/src/primitives/Button.tsx`
- [ ] `packages/base/src/primitives/Card.tsx`
- [ ] `packages/base/src/primitives/Checkbox.tsx`
- [ ] `packages/base/src/primitives/ColorSwatch.tsx`
- [ ] `packages/base/src/primitives/Combobox.tsx`
- [ ] `packages/base/src/primitives/ComboboxField.tsx`
- [ ] `packages/base/src/primitives/Divider.tsx`
- [ ] `packages/base/src/primitives/Dot.tsx`
- [ ] `packages/base/src/primitives/Dropzone.tsx`
- [ ] `packages/base/src/primitives/FileIcon.tsx`
- [ ] `packages/base/src/primitives/Kbd.tsx`
- [ ] `packages/base/src/primitives/Popover.tsx`
- [ ] `packages/base/src/primitives/Progress.tsx`
- [ ] `packages/base/src/primitives/SearchInput.tsx`
- [ ] `packages/base/src/primitives/Select.tsx`
- [ ] `packages/base/src/primitives/Tag.tsx`
- [ ] `packages/base/src/primitives/TextInput.tsx`

### Base Shell, Styles, Toolbars

- [ ] `packages/base/src/shell/ConsoleAppShell.tsx`
- [ ] `packages/base/src/shell/ConsoleShell.tsx`
- [ ] `packages/base/src/shell/default-chatter.tsx`
- [ ] `packages/base/src/shell/PublicAppShell.tsx`
- [ ] `packages/base/src/shell/PublicShell.tsx`
- [ ] `packages/base/src/shell/RevisionsChatterTab.tsx`
- [ ] `packages/base/src/shell/Shell.tsx`
- [ ] `packages/base/src/styles/index.css`
- [ ] `packages/base/src/styles/tokens.css`
- [ ] `packages/base/src/toolbars/CollectionDataToolbar.tsx`
- [ ] `packages/base/src/toolbars/DataToolbar.tsx`

### Base UI

- [ ] `packages/base/src/ui/accordion.tsx`
- [ ] `packages/base/src/ui/alert-dialog.tsx`
- [ ] `packages/base/src/ui/alert.tsx`
- [ ] `packages/base/src/ui/avatar.tsx`
- [ ] `packages/base/src/ui/badge.tsx`
- [ ] `packages/base/src/ui/button.tsx`
- [ ] `packages/base/src/ui/calendar.tsx`
- [ ] `packages/base/src/ui/card.tsx`
- [ ] `packages/base/src/ui/checkbox.tsx`
- [ ] `packages/base/src/ui/chip.tsx`
- [ ] `packages/base/src/ui/code.tsx`
- [ ] `packages/base/src/ui/collapsible.tsx`
- [ ] `packages/base/src/ui/command.tsx`
- [ ] `packages/base/src/ui/context-menu.tsx`
- [ ] `packages/base/src/ui/dialog.tsx`
- [ ] `packages/base/src/ui/drawer.tsx`
- [ ] `packages/base/src/ui/dropdown-menu.tsx`
- [ ] `packages/base/src/ui/field.tsx`
- [ ] `packages/base/src/ui/form-layout.tsx`
- [ ] `packages/base/src/ui/form.tsx`
- [ ] `packages/base/src/ui/input.tsx`
- [ ] `packages/base/src/ui/kbd.tsx`
- [ ] `packages/base/src/ui/kinded-avatar.tsx`
- [ ] `packages/base/src/ui/label.tsx`
- [ ] `packages/base/src/ui/nav-link.tsx`
- [ ] `packages/base/src/ui/navigation-menu.tsx`
- [ ] `packages/base/src/ui/number-field.tsx`
- [ ] `packages/base/src/ui/pagination.tsx`
- [ ] `packages/base/src/ui/popover.tsx`
- [ ] `packages/base/src/ui/preview-frame.tsx`
- [ ] `packages/base/src/ui/radio-group.tsx`
- [ ] `packages/base/src/ui/scroll-area.tsx`
- [ ] `packages/base/src/ui/section-eyebrow.tsx`
- [ ] `packages/base/src/ui/select.tsx`
- [ ] `packages/base/src/ui/selection-bar.tsx`
- [ ] `packages/base/src/ui/separator.tsx`
- [ ] `packages/base/src/ui/skeleton.tsx`
- [ ] `packages/base/src/ui/slider.tsx`
- [ ] `packages/base/src/ui/spinner.tsx`
- [ ] `packages/base/src/ui/status-icon.tsx`
- [ ] `packages/base/src/ui/switch.tsx`
- [ ] `packages/base/src/ui/table.tsx`
- [ ] `packages/base/src/ui/tabs.tsx`
- [ ] `packages/base/src/ui/text-link.tsx`
- [ ] `packages/base/src/ui/textarea.tsx`
- [ ] `packages/base/src/ui/toggle-group.tsx`
- [ ] `packages/base/src/ui/toggle.tsx`
- [ ] `packages/base/src/ui/toolbar.tsx`
- [ ] `packages/base/src/ui/tooltip.tsx`
- [ ] `packages/base/src/ui/tree.tsx`
- [ ] `packages/base/src/ui/widget-control.ts`

### Base Upload

- [ ] `packages/base/src/upload/client.ts`
- [ ] `packages/base/src/upload/index.ts`
- [ ] `packages/base/src/upload/queue.tsx`
- [ ] `packages/base/src/upload/types.ts`

### Base Views

- [ ] `packages/base/src/views/_filter-pipeline.ts`
- [ ] `packages/base/src/views/_provider.tsx`
- [ ] `packages/base/src/views/aggregate-buckets.tsx`
- [ ] `packages/base/src/views/board-view-model.ts`
- [ ] `packages/base/src/views/BoardView.tsx`
- [ ] `packages/base/src/views/CalendarView.tsx`
- [ ] `packages/base/src/views/dashboard/Metric.tsx`
- [ ] `packages/base/src/views/DashboardView.tsx`
- [ ] `packages/base/src/views/data-view-model.ts`
- [ ] `packages/base/src/views/data-view-table.ts`
- [ ] `packages/base/src/views/DataPage.tsx`
- [ ] `packages/base/src/views/form-layout/context.tsx`
- [ ] `packages/base/src/views/form-layout/Field.tsx`
- [ ] `packages/base/src/views/form-layout/Group.tsx`
- [ ] `packages/base/src/views/form-layout/header-context.tsx`
- [ ] `packages/base/src/views/form-layout/Header.tsx`
- [ ] `packages/base/src/views/form-layout/index.ts`
- [ ] `packages/base/src/views/form-layout/Notebook.tsx`
- [ ] `packages/base/src/views/form-layout/Panel.tsx`
- [ ] `packages/base/src/views/form-layout/Relation.tsx`
- [ ] `packages/base/src/views/form-layout/SmartButton.tsx`
- [ ] `packages/base/src/views/form-layout/StatusBar.tsx`
- [ ] `packages/base/src/views/form-layout/Tab.tsx`
- [ ] `packages/base/src/views/form-layout/Title.tsx`
- [ ] `packages/base/src/views/form-view-model.ts`
- [ ] `packages/base/src/views/FormView.tsx`
- [ ] `packages/base/src/views/GalleryView.tsx`
- [ ] `packages/base/src/views/GanttView.tsx`
- [ ] `packages/base/src/views/GraphView.tsx`
- [ ] `packages/base/src/views/list-view-overlays.tsx`
- [ ] `packages/base/src/views/ListView.tsx`
- [ ] `packages/base/src/views/page/Action.tsx`
- [ ] `packages/base/src/views/page/Actions.tsx`
- [ ] `packages/base/src/views/page/Column.tsx`
- [ ] `packages/base/src/views/page/index.ts`
- [ ] `packages/base/src/views/page/Section.tsx`
- [ ] `packages/base/src/views/page/types.ts`
- [ ] `packages/base/src/views/page/Views.tsx`
- [ ] `packages/base/src/views/RecordFrame.tsx`
- [ ] `packages/base/src/views/resource-page-introspection.ts`
- [ ] `packages/base/src/views/ResourcePage.tsx`
- [ ] `packages/base/src/views/TimelineView.tsx`
- [ ] `packages/base/src/views/TreeView.tsx`
- [ ] `packages/base/src/views/useResourceAction.ts`

### Base Widgets

- [ ] `packages/base/src/widgets/_date-field.tsx`
- [ ] `packages/base/src/widgets/boolean.tsx`
- [ ] `packages/base/src/widgets/booleanToggle.tsx`
- [ ] `packages/base/src/widgets/date.tsx`
- [ ] `packages/base/src/widgets/datetime.tsx`
- [ ] `packages/base/src/widgets/email.tsx`
- [ ] `packages/base/src/widgets/float.tsx`
- [ ] `packages/base/src/widgets/index.ts`
- [ ] `packages/base/src/widgets/integer.tsx`
- [ ] `packages/base/src/widgets/json.tsx`
- [ ] `packages/base/src/widgets/many2many.tsx`
- [ ] `packages/base/src/widgets/many2one.tsx`
- [ ] `packages/base/src/widgets/markdown-codemirror.ts`
- [ ] `packages/base/src/widgets/markdown.tsx`
- [ ] `packages/base/src/widgets/ownerCell.tsx`
- [ ] `packages/base/src/widgets/phone.tsx`
- [ ] `packages/base/src/widgets/progressBar.tsx`
- [ ] `packages/base/src/widgets/ribbon.tsx`
- [ ] `packages/base/src/widgets/selection.tsx`
- [ ] `packages/base/src/widgets/statusBadge.tsx`
- [ ] `packages/base/src/widgets/statusbar.tsx`
- [ ] `packages/base/src/widgets/tagInput-model.ts`
- [ ] `packages/base/src/widgets/tagInput.tsx`
- [ ] `packages/base/src/widgets/text.tsx`
- [ ] `packages/base/src/widgets/textarea.tsx`
- [ ] `packages/base/src/widgets/themePicker.tsx`
- [ ] `packages/base/src/widgets/url.tsx`

## Verification Checklist

- [ ] TypeScript workspace installs/links with the lifted packages.
- [ ] `@angee/sdk` exports all P1 SDK contracts required by `@angee/base`.
- [ ] `@angee/base` exports all P1 components used by addon UI packages.
- [ ] The host `createApp` call compiles with all addon manifests.
- [ ] Route tree includes every P1 route and preserves shell type.
- [ ] Menus render every P1 rail/tab/rail-subnav entry in deterministic order.
- [ ] Notes list renders title, tags, status, word count aggregate, updated-at.
- [ ] Notes toolbar renders search, filters, group-by, favorites, pagination,
      page-size selector, view switcher, and New note.
- [ ] Notes board groups by status in `STATUS_ORDER`.
- [ ] Notes form renders title, statusbar, details, live word counter,
      read-only audit fields, and markdown editor.
- [ ] Notes Activity chatter tab loads revisions and badge count.
- [ ] Auth login renders username/password, OAuth method slot, and demo footer
      slot.
- [ ] Storage page can browse files, switch drives, show folder tree, preview
      files, upload files, and show upload tray.
- [ ] Agents chatter tab renders in the chatter aside on all supported scopes.
- [ ] Angee, Knowledge, Operator, Integrate, Auth, Storage, and Agents admin
      route placeholders/pages mount without missing components.
- [ ] Role-gated routes deny access in shell with the expected empty state.
- [ ] Persisted queries/mutations invalidate and reset client as in P1.
- [ ] No generated runtime files are manually patched.
