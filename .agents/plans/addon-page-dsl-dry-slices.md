# Addon UX DRY: declarative pages, derived chrome, slice by slice

**Goal (architect, 2026-06-07):** An addon page should read like an Odoo view —
clean declarative TSX stating only addon facts (which fields, which widgets,
which defaults). Everything derivable (labels, enum options, filter/group
options, tones, route↔menu chrome, record navigation glue) moves to the level
that owns it. The addon **keeps explicit `routes:` and `menus:` declarations**
— we delete restatement and imperative glue, never the contract. The host
composes routes/menus/views exactly as today.

Plan-reviewed 2026-06-07 (findings folded). **Router pivot (architect,
2026-06-07):** after prior-art research (TanStack docs/examples, Refine,
React-Admin, Ant Pro/Umi, Remix `handle`, Odoo), the chrome *mechanism* is
TanStack-native — nested route tree, typed `staticData`, `useMatches()` —
replacing D1's hand-rolled breadcrumb stack (see D1b). Two research facts
anchor the design: (a) menu hierarchy must stay a **separate cross-referencing
declaration** (every framework that conflated menu tree with route tree grew a
divergence-flag zoo; `/iam/users` needs *Identity → Users* where "Identity" is
a routeless menu group — route ancestry alone is wrong); (b) the breadcrumb
trail is a **pure function of the match stack** (the push/pop provider
duplicated `useMatches`; context-accumulated crumbs are the documented
anti-pattern). Master–detail nesting is the kitchen-sink invoices pattern.

Two standing consequences from plan review: **(a)** D4 and D6 each contain a
small backend step (enum labels via `TextChoices` → SDL descriptions; the
revisions query for `RevisionMixin` models) because those owners live in
`angee.graphql`. **(b)** Every deleting slice is **pin-then-delete**: capture
today's behavior in tests first, then delete against that pin.

**Branch:** all slices land on `ui-refactor-dry`.

**Execution protocol:** one slice at a time via the Codex plugin. Codex's
in-sandbox verify is **typecheck + vitest only** (network installs and servers
hang the sandbox — pnpm installs are done by the supervisor on the host).
After each slice, in the host session: run the reviewers
(architecture-reviewer + react-reviewer on the slice diff; django-reviewer
when `angee/` Python is touched), then Playwright e2e against the running
stack:

```sh
pnpm run typecheck && pnpm run test          # repo-wide, from root
# stack via `angee dev` (root); if Python/schema changed:
#   uv run examples/notes-angee/manage.py angee build && … migrate && … schema
pnpm --filter @angee-example/notes-e2e test:e2e
```

A slice is done only when typecheck + vitest + e2e are green and the
reviewers pass. Each slice leaves the repo shippable.

**e2e baseline note (2026-06-07):** the suite assumes the workspace bootstrap
data — fresh DB + `resources load --include-demo` + `seed_lorem_notes`
(deterministic: 10k notes, faker seed 42). The root stack DB was rebuilt to
that state (old DB in `.angee/data/backup-2026-06-07/`); two specs were
reconciled to current main behavior in commit 33f3c00.

## Current state (the boilerplate inventory)

- `examples/notes-angee/addons/example/notes/web/src/NotePage.tsx` — 435 lines
  for "a list, a form, a chatter". ~75 lines of route/record state glue,
  ~70 lines of descriptor-literal form fields (each field listed twice:
  `formFields` + `formGroups`), ~50 lines of filter/group options derivable
  from field types, ~165 lines of chatter/activity panel duplicating fragments,
  hardcoded smart-button counts (7/12/4/23).
- `examples/.../notes/web/src/note-status.ts` — duplicates the backend
  `TextChoices` enum (labels) *and* `DEFAULT_STATE_TONE_VALUES`
  (`packages/base/src/lib/tones.ts:98`), with both casings of every key.
- `addons/angee/iam/web/src/index.ts` — was 136 lines; every console screen
  stated 3×. D1 cut the restatement; D1b's id-defaulting cuts more.
- `addons/angee/operator/web/src/index.ts` — was 171 lines; had hand-rolled
  its own sections→routes/menus derivation (the framework-gap smoking gun).
  D1 deleted the local machinery.
- `addons/angee/iam/web/src/views/ConnectionsPage.tsx` — 1,430 lines;
  re-implements the form layer (local field components at `:1127-1224`,
  three hand-rolled dialogs + payload mappers, `statusVariant` at `:1420`
  duplicating tones).
- `addons/angee/iam/web/src/views/UsersPage.tsx` — authored query + manual
  `useRegisterModelRefetch`; render-prop closures standing in for missing
  widgets. `RolesPage.tsx` (60 lines) is the gold standard.
- Operator sections talk to the daemon's JSON transport — legitimately
  bespoke; they participate only in the manifest slices.

Target: `NotePage.tsx` ≈ 30 lines of pure notes facts; iam manifest ≈ 60
lines; ConnectionsPage onto FormView/DialogForm + widgets.

---

## Slice D1 — manifest cross-reference + derived chrome (SHIPPED, mechanism reworked by D1b)

What D1 delivered and **survives unchanged**:

- `MenuItem.route?: string` (sdk `define-addon.ts`) — menu items reference
  routes by name; `to` resolves from the route's path; `route`+`to` on one
  item is a build-time error.
- Derivation rules: `title`/`icon` = **root** menu ancestor's label/icon;
  breadcrumbs = ancestors **linked**, leaf **plain**; `BaseAddonRoute.menu`
  adopts a menu position's trail for routes not in any menu (record routes),
  with the adopted item **linked**; per-field explicit chrome always wins;
  multi-reference without `menu:` fails fast. Named deliberate change: iam's
  ancestor crumbs became links.
- Adoption: iam menu uses `route:` refs, console routes carry no chrome;
  operator's `childBreadcrumb`/`sectionRouteName` machinery deleted; chrome
  pins for all three addons in
  `examples/notes-angee/web/src/addon-chrome.test.tsx`.
- Review fixes applied in the working tree (menu-id claim, cycle guard,
  per-field explicit chrome, operator i18n keyed by menu-item id, hermetic
  test fetch, host test deps, docstrings).

What D1 built that **D1b deletes**: the runtime breadcrumb stack
(`BreadcrumbProvider` push/pop, DataPage's push effects, the stack unit
test). The trail content was right; the mechanism re-implemented the router.

## Slice D1b — TanStack-native chrome: nested tree, staticData, useMatches

**Owner map:** the route tree, layout persistence, matched-branch enumeration,
and per-route typed metadata are TanStack Router's (code-based routing,
`staticData`, `useMatches`/`isMatch`; kitchen-sink invoices = the
master-detail shape). The menu-derived trail *content* stays `MenuTree`'s
(unchanged D1 derivation); the composer changes where it *writes* the result.

**Contract:**

- **Manifest additions** (both declarations stay explicit):
  - Menu-item `id` **defaults to `route`** when omitted (architect,
    2026-06-07); explicit `id` still allowed; ids still claim/collide
    fail-fast. Delete the now-redundant `id:` lines in iam/operator/notes
    menus.
  - `BaseAddonRoute.parent?: string` (route name) — nests this route under
    another route in the runtime tree. Validation (fail-fast): parent exists,
    same shell, parent path is a proper path-prefix. **No auto-nesting by
    path containment** — sibling pages (`/iam`, `/iam/users`) stay siblings;
    nesting is declared (the Camp-A trap avoided at the tree level).
  - `BaseAddonRoute.crumb?: (match) => ReactNode` — dynamic crumb factory,
    written to `staticData.breadcrumb`. The factory's component self-fetches
    (urql cache) — never router loaders (urql owns data; stack rule).
  - `BaseAddonRoute.component` becomes optional for `parent:`-nested routes
    (the parent surface reads child params via `useParams({strict:false})`).
- **Nested runtime tree** in `createApp`: one pathless layout route per shell
  (`id`, shell chrome component rendering `<Outlet/>`); addon routes nest
  under their shell layout; `parent:` refs nest further. Deterministic child
  order (sort by route name). Chrome mounts once per shell — explicit
  persistence instead of reconciliation luck.
- **Typed `staticData`** (module augmentation lives in `@angee/base`, the
  package every addon imports — the monorepo-example rationale):
  `StaticDataRouteOption { chrome?: { title, icon, breadcrumbs }, breadcrumb?: (match) => ReactNode }`.
  The composer writes MenuTree-derived chrome onto each route's staticData;
  the `chromePropsByRoute` plumbing dies.
- **Consumers read matches:** shell chrome (TopBar title/icon) = deepest
  match carrying `chrome`; `Breadcrumb` renders the static trail of the
  deepest match that has one, then the `breadcrumb` factories of matches
  **below** it, in match order. Pure function of `useMatches()` — no provider
  state. `BreadcrumbProvider`/`pushItem` deleted.
- **DataPage**: both record-crumb push effects deleted;
  `recordBreadcrumbLabel` logic moves into the notes record route's `crumb`
  component (`NoteCrumb`: `useResourceRecord("notes.Note", id, {fields:["title"]})`
  — cache hit after list load; `id === "new"` → "New"). D4 later generalizes
  this to a `RecordCrumb(model)` via `recordRepresentation` metadata
  (React-Admin's insight: one per-model "how a record displays" fact).
- **notes adoption:** the record route entry gains `parent: "notes.home"` +
  `crumb`; loses its `component` (delete `NoteRecordPage` — `NotePage` reads
  `useParams({strict:false})` itself; the navigation glue still dies in D3).
- **Pins:** the addon-chrome pins survive — they assert rendered chrome, not
  the mechanism. Add: nested-tree shape assertions (`routesById` parents),
  crumb-factory rendering, child params reaching the parent surface. Delete
  the stack unit test with the stack.

**Checks:** root typecheck + vitest; full e2e suite (trail and title/icon
render exactly as the pins say; record crumb appears when a note opens).

## Slice D2 — DataPage accepts the Page DSL

**Owner:** `DataPage` (`packages/base/src/views/DataPage.tsx`); markers/parsers
already exist in `packages/base/src/views/page/`. Note: `parsePageFields`
(`page/index.ts:48-60`) already recurses into groups — declaring a field once
inside `<Group>` already yields the flat field list.

**Contract (architect, 2026-06-07: `List`/`Form` must be REUSABLE views, not
inline-only markers — the Odoo shape: views exist standalone; the action
stitches them):**

- `List` and `Form` are **real components** in `views/`, with two faces:
  - **Standalone render:** `<List model="notes.Note" …list props…>
    <Column …/>…</List>` renders the collection view itself (the existing
    ListView/GroupListView under the hood); `<Form model="…" id={…}>
    <Field …/><Group …/></Form>` renders a FormView. Usable on their own
    routes, inside `DialogForm`, anywhere.
  - **Inside `<DataPage>`:** DataPage recognizes its `List`/`Form` children
    by element type (the `PageElementKind` mechanism), parses their children
    with the existing `parsePage*` parsers, and composes the master-detail
    surface — one render path owned by DataPage (parse, don't render-then-
    clone).
- **Reuse shape (the point):** an addon declares the element once
  (`export const noteForm = <Form>…</Form>`) and hands the same declaration
  to DataPage, a dialog, or a standalone route. One view declaration, many
  surfaces — D5's ConnectionsPage dialogs consume this.
- `model` (and data-view context) is **inherited** from DataPage when nested;
  explicit when standalone; declaring both with different values is a
  build-time error.
- Section-level props ride on the elements: `<List>` carries `filters`,
  `filterFields`, `groupOptions`, `order`, `pageSize`; `<Form>` carries
  `returning` etc.
- Array props remain the programmatic API. **One collision rule at the
  owner:** declaring both children and the corresponding array prop is a
  build-time error — and `FormView` is brought onto the same rule (today it
  silently prefers `fields ?? parsePageFields(children)` —
  `FormView.tsx:103-110`).

**Adoption:** rewrite `NotePage.tsx`'s descriptors as reusable
`<List>`/`<Form>` declarations consumed by `<DataPage>`. Delete the field
consts and `formFields`/`formGroups` arrays.

**Checks:** unit tests for the new parsers + the both-sources error (DataPage
*and* FormView); existing suites; e2e `notes-form.spec.ts`,
`notes-form-interactions.spec.ts`, `notes-views.spec.ts` unchanged.

## Slice D3 — DataPage owns record-route navigation

**Owner:** `DataPage` + the router. Routes stay in the manifest; D1b already
nests the record route and moved param-reading into the parent surface. D3
removes the remaining imperative glue: navigation.

**Contract:**

- **Implement the `"new"` sentinel first.** `DataPage.tsx:67` *documents*
  that `recordId === "new"` opens a blank form, but the body treats it as an
  edit (`:188-190`). Reconcile at the owner: map `"new"` to create mode in
  `DataPageBody`. Then wire create → `${base}/new`.
- Routed mode: `DataPage` derives the collection base path **from the matched
  route** (the router owns route facts; strip the child param segment); a
  `path` prop exists only as an explicit override. In routed mode DataPage
  owns select/close navigation: select → `${base}/${encodeURIComponent(id)}`,
  close → `base`, create → `${base}/new`; `rowHref` defaults to the record
  path; URI decode owned here.
- Controlled mode (`recordId`/`onSelect`/`onClose` props) remains. Mixing
  routed mode with controlled props is a build-time error.

**Pin first:** add the deep-link spec cases before deleting the glue: open
`/notes/$id` directly (form shows), browser back returns to the list,
`/notes/new` shows a blank create form.

**Adoption:** `NotePage.tsx` deletes the `useState`/`useEffect` record sync,
`handleSelect`/`handleClose`, `noteRecordPath`, `routeRecordId`.

**Checks:** vitest for the sentinel + collision; the new deep-link e2e cases;
existing `notes.spec.ts` unchanged.

## Slice D4 — schema-derived field metadata (the `fields_get` equivalent)

**Owner map:** human enum labels are owned by the Django `TextChoices`
(`examples/.../notes/models.py:31-37` — `"In Review"`); the SDL is the wire;
the SDK owns SDL ingestion (beside `cacheConfigFromSDL`); defaults are
applied by the views/widgets that consume the metadata.

**Contract:**

- **Backend step (in scope, small):** `angee.graphql` emits `TextChoices`
  labels as enum-value descriptions in the SDL (today none —
  `runtime/schemas/public.graphql:602-607`). Regenerate runtime SDL. Gate:
  django-reviewer + `manage.py schema --check`.
- **SDL reaches the SDK:** `AngeeUrqlClientOptions` gains `sdl`; `cache`
  derives from it when absent (explicit `cache` stays an override); the
  host's `cacheConfigFromSDL` calls (`examples/notes-angee/web/src/main.tsx:41-42`)
  are deleted.
- SDK: `fieldMetadataFromSDL(sdl)` → per type, per field: kind, scalar name,
  enum values + labels (descriptions; fallback below), relation target.
  Exposed via a provider + `useModelMetadata(modelLabel)`, wired through
  `createApp`. Include the per-model **`recordRepresentation`** (title-ish
  display field) consumed by `RecordCrumb`, page titles, and reference
  widgets.
- **Label fallback owners are the existing helpers**, not bare `titleCase`
  (`titleCase("IN_REVIEW")` → `"IN REVIEW"`, fails `notes-grouped.spec.ts:66`):
  `statusLabel` (`ListInternals.tsx:1090`) for enum values, `groupFieldLabel`
  (`ListInternals.tsx:1085`) for field names.
- Defaults derived where the consumer owns the decision: column/field labels;
  widget enum `options`; `filterFields` from column kinds (string → text,
  enum → selection, datetime → datetime); `groupOptions` (datetime → date
  group + granularities, enum → value group); enum filter chips; tones stay
  `statusBadge`-automatic with column `tone` maps as overrides only.
- New widget: `booleanBadge` (labeled true/false badge with tones).

**Adoption:** delete `note-status.ts`; NotePage drops `NOTE_FILTERS`,
`NOTE_FILTER_FIELDS`, `NOTE_GROUPS`, and every label the derivation
reproduces. **Q2 resolved (architect):** `IN_REVIEW → info` was an accident —
the derived default (`warning`) is correct; no override survives; the badge
change is a named deliberate change.

**Checks:** SDK vitest for `fieldMetadataFromSDL` against the regenerated
example SDL; base vitest for the derivations; e2e `notes-grouped.spec.ts`,
`notes-views.spec.ts` unchanged.

## Slice D5 — iam adoption: port pages onto the primitives

Consumer-level cleanup that **stress-tests D2/D4**; gaps found are fixed at
the owning framework level inside this slice.

- `ConnectionsPage.tsx`: investigate CRUD availability for
  `OAuthClient`/vendor/external-account in the console schema; port the three
  dialogs to `DialogForm`/`FormView` + widgets (+ `useResourceMutation` if
  served); delete the local field components (`:1127-1224`); `statusVariant`
  (`:1420`) → tones. Target well under 500 lines.
- `UsersPage.tsx`: `useResourceList` if served (kills authored query + manual
  refetch registration); render closures → `booleanBadge` + a name-concat
  field (prefer the backend owner — one resolver).
- `RolesPage.tsx`: drop derivable labels/group declarations.

**Checks:** iam vitest suites updated alongside; e2e `iam-console.spec.ts`,
`iam-auth.spec.ts` green.

## Slice D6 — chatter revisions + fragments hygiene

**Owner map:** "revision-tracked models are queryable" is owned by
`angee.graphql` (per `docs/stack.md`, django-reversion row). Today
`noteRevisions` is a **hand-authored consumer resolver**
(`examples/.../notes/schema.py:174`). Fix at the owner first.

- **Backend step (in scope):** `angee.graphql` emits the revisions query for
  `RevisionMixin` models the way `crud` emits CRUD; delete the notes
  consumer resolver. Detection falls out of the SDL (D4 metadata).
  Regenerate SDL; gate: django-reviewer + `schema --check`.
- SDK: `useResourceRevisions(model, id)` — mechanically assembled document +
  typed result.
- Base: `RevisionsTab` chatter contribution built from a `TimelineEntry`
  fragment + `EmptyState`/`LoadingPanel`/`ErrorBanner`.
- Base: `RelativeTime` primitive (date-fns owner) — **adopt it at the
  existing inline call sites in the same slice** (`ListInternals.tsx:858`).
  `excerpt` folds into `TimelineEntry`.
- Notes: `NoteChatter`/`NoteActivityPanel`/`RailEmptyState` deleted.
  **Q3 resolved:** placeholder Angee/Comments tabs stay, with no fake counts.
  **Q4 resolved:** smart buttons keep only `versions`, wired to the real
  revisions count; `linked`/`comments`/`attachments` (fake 7/12/4) deleted.

**Checks:** chatter e2e assertions live in
`notes-form-interactions.spec.ts:55-65` and `notes-functions.spec.ts:100-105`
— update only where fake data is removed.

---

## Invariants (every slice)

- Addons keep explicit `routes:` and `menus:` declarations; the host composes.
  No route registration hidden inside view components.
- **Defer to the stack:** the router owns tree/matches/staticData; urql owns
  data (crumbs self-fetch, never router loaders); MenuTree owns menu
  hierarchy facts.
- **Pin-then-delete:** capture today's behavior in a test first, then delete
  against that pin. Never claim an e2e assertion that doesn't exist; never
  weaken a spec to make a slice pass.
- Derivation reproduces today's UI except where a slice *names* a deliberate
  change (D1: iam ancestor links; D4: In Review badge tone).
- Explicit declaration always beats derivation; collisions/ambiguity are
  build-time errors, never silent precedence.
- One source of truth per fact; no copying between addons.
- Scope: frontend + SDK, plus the two owner-level backend steps (D4 enum
  descriptions, D6 revisions emission), each gated by django-reviewer,
  `manage.py schema --check`, regenerated runtime SDL.

## Decisions (architect) — all resolved, folded into the slices

- **Q1:** breadcrumb trail confirmed (ancestors linked, leaf plain; iam links
  deliberate) — superseded in mechanism by the router pivot: the "stack" is
  the router's match stack (D1b), not owned provider state.
- **Q2:** `IN_REVIEW → info` was an accident; derived `warning` correct.
- **Q3:** keep placeholder chatter tabs; no fake counts.
- **Q4:** smart buttons only truly-implementable — `versions` w/ real count.
- **Router pivot (2026-06-07):** TanStack-native chrome (D1b) — approved.
- **Menu id defaulting (2026-06-07):** menu-item `id` defaults to `route` —
  approved; explicit ids remain legal; collisions still fail fast.

## Slice gate (repeat after every slice)

1. Codex executes the slice; in-sandbox verify: package typecheck + vitest.
   (pnpm installs run on the host — the sandbox has no network.)
2. Host session: `pnpm run typecheck && pnpm run test` from root.
3. Reviewers on the slice diff: architecture-reviewer + react-reviewer;
   django-reviewer whenever `angee/` Python is touched (D4, D6).
4. Stack up (`angee dev`); if Python/schema changed: `angee build`,
   `migrate`, `manage.py schema` (+ `--check`); then
   `pnpm --filter @angee-example/notes-e2e test:e2e`.
5. Update the resume-state line below; commit each green slice on
   `ui-refactor-dry` (terse message, no AI attribution).

**Resume state:** chrome slice committed as 050e087; D2 (reusable List/Form +
DataPage DSL, both reviewers folded incl. 13-item round, naming decision: DSL
owns bare element names) COMMITTED as 9502155 — gated (typecheck, 270 vitest,
69/69 e2e). NEXT: D3 — DataPage owns record-route navigation ("new" sentinel
first, base path from matched route, routed/controlled collision; pin
deep-link e2e cases before deleting NotePage's navigation glue).
