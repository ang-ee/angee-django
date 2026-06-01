# M3 Frontend — production rebuild (detailed, codex-executable)

This supersedes the lean cut in `m3-frontend.md`. We are **rebuilding a production
system**, not a demo: the full notes+auth experience the prototype ships, on the
new offset/enum GraphQL contract. Three target screens are the acceptance bar:

1. **Login** — split hero: left marketing panel (logo/wordmark, ALPHA eyebrow,
   gradient headline, copy, background image); right "Welcome back" card with
   labeled username/password, Sign-in, and a boxed "Demo users" footer.
2. **Notes list** — full console: left icon rail, top bar (title, tabs
   All/Starred/Archive, ⌘K global search, notification + help systray, user menu),
   breadcrumb, control band (New note, removable "Group by: Updated · Day" chip,
   Filter…, right cluster: pager "1–50 / 50" + list/board view-switch), the
   date-**grouped** grid (group header with count + word-count total; columns
   Title / Tags chips / Status badges / Word Count / Updated At relative; row
   checkboxes; sortable headers), and the right **assistant rail** ("Angee — No
   agent yet / Set up your assistant").
3. **Note record** — breadcrumb; status **stepper** (Draft → In Review → Active →
   Archived) top-right; record pager "14 of 50" + view-switch; sectioned form —
   **Details** (tag-chip input, Word Count, Created By / Updated By, Created At /
   Updated At in a 2-col grid) and **Body** (Markdown editor: toolbar
   B/I/code/list/quote/link + code/preview toggle + line numbers); right rail with
   Angee / Comments / Activity tabs.

## The bar (this is framework code, not a demo)

`@angee/base` is the framework's single rendered binding — inherited (copied) by
every downstream project — and the notes addon is the reference every consumer
learns from. Both are held to the **highest bar** (AGENTS.md → Repository Role).
Every phase is judged against `docs/guidelines.md`: DRY, **find the owner** (behavior
on the owning object, not loose shape-decoding helpers), **name so code can be
found** (one concept one name; follow the host framework's conventions), defer to
the stack, and the red flags (bigger-not-smarter, spaghetti, code-you-don't-
understand, reinventing a tested wheel). No stubs-as-final, no "lean" shortcuts, no
dead code. Each phase ends with an `architecture-reviewer` + `react-reviewer` pass;
their confirmed findings are fixed before the phase commits.

## Method (every phase)

- **Reuse the prototype's clean code; reconstruct on the new SDK.** The prototype's
  base is already on our stack — port components faithfully, then rewire their data
  layer from the old SDK (persisted ops / `DataViewProvider` / relay edges /
  `fieldMeta` magic) to ours (`useResourceList` offset, `useResourceRecord`,
  `useResourceMutation`, `useResourceGroupBy`, the runtime `useWidget`/`useMenus`/
  `useSlot`). **Strip ALL provenance** (no prototype/mockup/plan-number/lifted refs,
  no nonexistent doc paths). **No file is byte-identical to a prototype file.**
- **Production quality:** typed (no `any` at boundaries — narrow `unknown`),
  accessible (labels, roles, keyboard), no dead code, gate-green per phase.
- **Defer to the stack** for deps (below); add a `docs/stack.md` owner row in the
  same change as any new dependency.
- **Reference (internal only):** `/Users/alexis/Work/fyltr/angee-django-p1` — the
  file maps per screen are in `.agents/notes/lift-auth-graphql/` inventory and
  inline below. Reconstruct from it; never copy.

## What already exists (keep / extend) vs superseded

- **Keep as-is:** `@angee/sdk` (offset contract, enums, auth, invalidation, 106
  tests); `@angee/base` styling foundation (`lib/{cn,variants,tailwind-merge-config}`,
  `styles/{tokens,index}.css`); the 22 UI primitives; the example host scaffold
  (`examples/notes-angee/src/web` — vite/tailwind/index.html/CSRF) and `/auth/csrf/`.
- **Extend:** `createApp` (add the widget registry, query-state, modals/confirm,
  the chatter/assistant rail to the provider stack; richer route/shell wiring).
- **Superseded (replace with the full reconstructions):** the lean
  `views/{ListView,FormView,DataPage,AggregatePanel}.tsx`, `shell/{ConsoleShell,
  PublicShell}.tsx`, `auth/{LoginPage,UsernamePasswordForm}.tsx`. Keep their files
  as the starting point but rebuild to the full prototype shape.

## Architecture to reconstruct (cross-cutting — build before the screens)

These are the load-bearing seams the three screens compose; the prototype's
components assume them:

1. **Element DSL** (`base/views/page/{Column,Field,Group,Action}.tsx`): render-null
   marker components read by their parent view via `Children.toArray()` + slot
   symbols. `<Column field= widget= sortable= aggregate=>`, `<Field name= widget=
   label= readOnly= title=>`, `<Group label= columns=>`. This is how `NoteListView`/
   `NoteFormView` declare columns/fields.
2. **Data-view state model** (`base/views/data-view-model.ts` + a `DataViewProvider`):
   page/pageSize, sort, filter, group-by(field+granularity), selection, view kind.
   **Rebuild on TanStack Router search params** (stack-owned) — NOT `nuqs` — so
   back/forward restores UI state. Drives `useResourceList` (offset) +
   client-side grouping/aggregation of the loaded page.
3. **Widget registry**: field/cell renderers keyed by widget id, resolved via
   `useWidget(id)` (already in the SDK runtime). Base ships the default widgets
   (text, datetime, statusbar, tagInput, markdown.editor, markdown.preview,
   select, switch, relation/userRef); addons contribute/override (notes:
   word-counter, updated-at). **Field→widget mapping is declared explicitly per
   addon** (via the Element DSL / column+field config) — NOT auto-derived from a
   `fieldMeta` codegen artifact (simpler, explicit, "find the owner"; revisit
   schema-driven fieldMeta as a later enhancement).
4. **Slot system** (already in SDK: `useSlot`/`mergeSlotContributions`): the login
   page reads `AUTH_LOGIN_SLOT` regions ("method", "card-footer", "page-footer");
   the host contributes the demo-credentials hint.
5. **Chrome grid + chrome** (`base/shell/ConsoleShell` on the `console-grid`
   token utilities already in `styles/index.css`): `chrome/{AppRail, TopBar,
   TopMenu, GlobalSearch (cmdk Spotlight), Systray, Breadcrumb, Glyph + icon
   registry, UserMenu}`.
6. **Communication rail** (`base/communication/{Chatter, chatter-context}`): the
   right resizable panel with Angee/Comments/Activity tabs
   (`react-resizable-panels`). Angee + Comments are stubs ("No agent yet");
   Activity wires to `noteRevisions`.
7. **Modals/confirm** (`base/feedback/ModalsHost` + `useConfirm`): delete
   confirmation, unsaved-changes guard.

## Dependencies (architect's call: add the prototype's deps verbatim, max fidelity)

The prototype's frontend deps are proven and let its components port with the least
reconstruction. **Add each to `packages/base/package.json` AND add a `docs/stack.md`
owner row in the same change** (the stack rule: no dependency without an owner row,
one owner per concern):

- Already owner-rowed (add to the manifest, no new row): `cmdk`,
  `@codemirror/{state,view,commands,lang-markdown}` + `codemirror`,
  `react-resizable-panels`, `@dnd-kit/{core,sortable}`, `react-day-picker`,
  `@tanstack/react-{form,table,virtual}`, `valibot`, `lucide-react`,
  `tailwind-variants`/`tailwind-merge`/`tw-animate-css`.
- New owner rows to add (under Frontend / Rendered Binding): `nuqs` (URL view
  state), `date-fns` (date/relative formatting), `use-debounce` (debounced inputs),
  `@floating-ui/react-dom` (virtual-anchor positioning), `react-markdown` +
  `remark-gfm` (Markdown rendering/preview), `@angee/logo-react` (brand logo).
  Write a one-line "owns / Angee adds" row for each, grouped by concern.

One tension to record (not block on): `nuqs` overlaps TanStack Router's search-param
ownership. Going **verbatim per the fidelity call**; the owner-row notes nuqs as the
view-state serializer layered on the router. If you'd rather it be Router-native,
say so and I'll wire the data-view model on router search instead — no other change.

Genuinely not needed for notes+auth and left out unless a screen requires them:
`@xyflow/react`, `react-json-view-lite`, `ansi-to-react`.

## Backend additions (small; needed for fidelity)

1. **`IN_REVIEW` status.** The screens show 4 states (Draft / In Review / Active /
   Archived); the model has 3. Add `IN_REVIEW` to `Note.Status` (TextChoices),
   re-emit + migrate. Touches the `NoteStatus` enum, statusbar steps, demo data.
2. **`createdBy` / `updatedBy` on `NoteType`.** The form shows them as the user's
   opaque id (e.g. `usrgbHJdmfr`) — a **scalar public id (sqid) string**, NOT a
   nested `UserType` (so no REBAC-nested-user leak; this is why M1 dropped the FK
   expansion). Expose `createdBy: ID`/`updatedBy: ID` resolving the related user's
   `public_id`. (Audit-stamp the fields on create/update if not already.)
3. **`noteRevisions(id)`** query for the Activity tab — return the note's revision
   timeline (reversion `Version`s mapped to a small GraphQL type), REBAC-gated like
   `note(id)`. Built in P0; the Activity tab renders it for real in P6.
4. **Search** → no backend change: the ⌘K search and the Filter… box build a
   `title: { iContains }` (and tags) `NoteFilter` client-side.
5. **Word-count group total** → no backend change: the grid sums the loaded page's
   `wordCount` per client-side group (the prototype's "1,289 words" footer).
   (Server-side sum would need promoting `word_count` to a real column — out of scope.)

## Phases (each is a codex unit + a verification gate I run)

Ordering respects dependencies; each ends green (`pnpm -r typecheck`, the package's
vitest, host `vite build`; backend phases add ruff/mypy/pytest/example + the live
`angee dev` HTTP smoke). Commit at each phase.

- **P0 — Backend additions.** `IN_REVIEW` status (TextChoices + statusbar steps +
  demo data); `createdBy`/`updatedBy` scalars on `NoteType` resolving the related
  user's `public_id` (opaque id, no nested user); a real `noteRevisions(id)` query
  mapping reversion `Version`s to a small GraphQL type (REBAC-gated like `note`).
  Re-emit SDL; extend the example HTTP tests to cover all three. Gate: backend gate
  + `schema --check`.
- **P1 — View architecture seams.** Element DSL (`views/page/*`), data-view-model +
  `DataViewProvider` on TanStack Router search, the widget registry contract +
  default widgets (`widgets/{text,datetime,statusbar,tagInput,select,switch,
  userRef}.tsx`), `feedback/ModalsHost` + `useConfirm`. Gate: typecheck + unit tests
  for the model + DSL parsing.
- **P2 — Chrome + shells.** `chrome/{Glyph + icon registry, AppRail, TopBar,
  TopMenu, Systray, Breadcrumb, UserMenu, GlobalSearch}` (cmdk Spotlight),
  `communication/{Chatter, chatter-context}` (resizable), the full `ConsoleShell`
  on `console-grid`, and the inline logo. Gate: typecheck; a Storybook-less render
  smoke (mount under the test harness).
- **P3 — Login (Screen 1).** Full `PublicShell` (background image asset, hero grid),
  `auth/{LoginPage, HeroPanel, UsernamePasswordForm}` + `login-slot`, the host's
  demo-credentials slot. Gate: typecheck + the live login flow (HTTP smoke +
  screenshot).
- **P4 — Markdown editor widget.** `widgets/markdown.tsx` (CodeMirror 6 editor +
  toolbar + line numbers; preview via the chosen renderer). Gate: typecheck +
  widget unit test.
- **P5 — ListView (Screen 2).** Full `views/ListView.tsx` (TanStack Table +
  virtual; grouping with group-total footer; tag-chip / status-badge / relative-
  time cells; row selection + `SelectionBar`; sortable headers), `toolbars/
  DataToolbar` (New, group-by chip, Filter…, pager, list/board view-switch),
  `views/ResourcePage` + `DataPage` (collection routing), and the notes
  `NoteListView`/`NotePage` config. Gate: typecheck + the live list (paging,
  grouping, badges, search) screenshot.
- **P6 — FormView/record (Screen 3).** Full `views/FormView.tsx` + `form-layout/
  {Field,Group,header-context}`, the status stepper in the header, the record pager
  + view-switch via `ResourcePage`, `NoteFormView` (title header, statusbar, Details
  group with tagInput/word-count/audit/dates, Body markdown), and the Chatter
  Activity tab wired to `noteRevisions`. Gate: typecheck + live create/edit/status-
  transition screenshot.
- **P7 — Full gate + e2e.** `pnpm -r typecheck/test/build`, backend gate, `angee
  dev` up, browser walk-through of all three screens (login → list paging/grouping/
  filter → record edit/status), live `noteChanged` cross-client update. Reconcile
  `docs/frontend/guidelines.md` + `docs/stack.md` (new rows). Update STATE.

## P1 review findings (architecture + react) — fixes pending, apply after P2 lands

Codex's P1 (`6da9ff6`) over-built. Reviewers converged: it shipped premature,
duplicated, partly-buggy machinery. Fix before P5 consumes it (do these as one
"M3 P1 review" commit on top of P2, since P2 writes the same package):

- **HIGH — collapse the parallel vocabulary.** The Element-DSL `ColumnDescriptor`/
  `FieldDescriptor`/`PageFieldKind` duplicate `ListView`'s `ListColumn` /
  `FormView`'s `FormField`/`FieldKind`. ONE owner: when P5/P6 rebuild the full
  ListView/FormView they MUST consume the DSL descriptors and the lean
  `ListColumn`/`FormField`/`FieldKind` are deleted. Do not keep two shapes.
- **HIGH — `DataViewProvider.dispatch` stale-closure batch drop.** It recomputes the
  full query from a frozen `state` and overwrites all 7 URL keys, so two setters in
  one handler drop the first. Fix: single `useReducer` source of truth synced to the
  URL in one effect, or a functional `setQueryValues(prev => …)` that emits only the
  changed keys.
- **HIGH — selection in push-history.** `selection` is URL-synced under
  `history:"push"`, so checkbox toggles spam Back. Selection is ephemeral UI state
  (the stack doc scopes nuqs to page/sort/filter/group) — hold it in local state, or
  at least `history:"replace"`. Likely also drop `view` from pushed history.
- **MEDIUM — `statusbar` hardcodes the status ladder** into `@angee/base`. Drive the
  steps from `field.options` (schema/consumer owns the values; framework owns the
  renderer) — same pattern `select.tsx` already uses.
- **MEDIUM — `DataViewProvider`/`useDataView` mounted nowhere.** Mount it in P5 from
  the ListView page that consumes it; keep the tested pure `data-view-model.ts`.
- **MEDIUM — page-size default `50` duplicated.** Reuse the SDK's owner (export
  `DEFAULT_PAGE_SIZE` or derive from `PAGE_SIZE_OPTIONS`).
- **LOW — `userRef` guesses shape** (5-key fallback): pass a resolved label; the
  schema/selection owns which field is the display name.
- **LOW — `selection` name collides** with the SDK's GraphQL `selection`; rename the
  row concept to `selectedIds`.
- **LOW — `widgetLabel` lives in `types.ts`** (behavior in a types module) → move to
  `widgets/label.ts`. **LOW — DSL** should fail-fast on duplicate `field`/`name`/`id`
  and `pageElementProps` should be typed-by-kind. **LOW — `dataViewFilterFromUnknown`**
  trusts any object from the URL; validate at the SDK filter boundary.

## Execution

Codex executes phase-by-phase via the `codex:codex-rescue` subagent with a tight
per-phase brief (goal, the prototype source files to reconstruct, the target files,
the new-SDK wiring, deps, provenance rules, the gate). After each phase: the main
session re-runs the gate + (for UI phases) a live screenshot, reviews
(react-reviewer / architecture-reviewer), fixes, commits. Codex protocol per STATE:
attempt one commit per phase; if it can't, leave the tree and report — the main
session verifies + commits. No provenance in any artifact.
