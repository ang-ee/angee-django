# Console design system — page & layout catalog + build plan

**Goal (architect, 2026-06-07):** Every addon's console pages should compose
from a shared, finite set of **page presets + Views** — not hand-roll layout.
Today notes is clean (DataPage), but iam/operator each re-invent dashboards,
lists, status, and empty states, and the unbuilt addons (knowledge, storage,
agents, workflows) need Views that don't exist yet. The fix is to finish the
design-system vocabulary the p1 docs already specify, then make every addon an
*adopter*. This doc is the catalog + the build sequence; a mockup follows.

Source of truth for the vocabulary: p1 `docs/frontend/layouts.md` and
`shells.md`. Five layers: **Shell → Page → View → Element → Widget** — each
layer picks a framework component and fills its slots with the layer below.

## The vocabulary (target)

- **Shells** (chrome bound to a schema): `ConsoleAppShell` (console),
  `PublicShell` (login/oauth/marketing), `UserShell` (reserved).
- **Page presets** (own chrome, expose slots): `DataPage` (model chrome:
  toolbar·breadcrumb·pager·view-switcher·collection⇄record routing, +
  `navigator` ⇒ Explorer, + `aside`), `HeroPage` (centred single column),
  `CanvasPage` (full-bleed + optional toolbar/aside).
- **Views** (fill the page body, switchable): `ListView · FormView · BoardView
  · GalleryView · CalendarView · GanttView · TimelineView · TreeView ·
  GraphView · DashboardView`.
- **Elements** (compose inside a View): list `Column`; form `Header · Title ·
  StatusBar · Group · Field · Notebook · Tab · SmartButton · Action · Relation
  · PreviewPane`; dashboard `Metric · Card`.
- **Widgets**: the field renderers (`text · selection · many2one · markdown ·
  statusBadge · userRef · booleanBadge · …`) — already in good shape.

"Hubs/sections" are **not** a page type: distinct related models = a menu
group of routes; same data multiple renderings = the view-switcher; sections
of one record = a `FormView` `<Notebook>`.

## What ships today (current repo, after D1–D6)

| Layer | Shipped | Notes |
|---|---|---|
| Shells | ConsoleShell (+AppShell-ish via createApp), PublicShell | menu↔route chrome, breadcrumb-from-matches |
| Presets | DataPage (routed), HeroPage, CanvasPage, RecordView, SplitView | DataPage owns List/Form + routing |
| Views | ListView, GroupListView, BoardView, GraphView, RowsListView, FormView, AggregatePanel | AggregatePanel ≈ partial Dashboard |
| Elements | Column, Field, Group, Action (List/Form DSL); SmartButton (descriptor only) | reusable List/Form from D2 |
| Widgets | full set incl. booleanBadge, statusBadge, userRef, markdown | SDL-derived metadata (D4) |

## Missing Views/Elements (documented, needed by addons)

| Missing | Primary consumers | Effort |
|---|---|---|
| **DashboardView** (Metric/Card Elements, proper) | iam overview, operator overview, agents | S — generalize AggregatePanel + the metric-strip mockup |
| **TreeView** | knowledge (vault/folders), storage (folder tree) | M |
| **Explorer** (`DataPage navigator` slot = tree + list/gallery) | storage, knowledge | M (builds on TreeView) |
| **GalleryView** (card/thumbnail grid) | storage (files), knowledge, notes "Cards" | S–M (BoardView shares card shell) |
| **PreviewPane** (form Element + DataPage aside) | storage (file preview), knowledge | M |
| **TimelineView** (chronological) | agents (transcript/activity), notes revisions | S (RevisionsTab/TimelineEntry already exist — generalize) |
| **FormView `Notebook`/`Tab`** | agents wizard, multi-section records | M |
| **CalendarView** | scheduling, agents, calendar-base | M |
| **GanttView** | workflows (v1.1) | L — defer |
| **Form Elements** Header/Title/StatusBar/Relation | record chrome polish | S each |
| **CanvasPage editor wiring** (graph/chat) | knowledge canvas, agents chat, workflows editor | M per surface |

## Page-archetype catalog (addon × page → preset+View)

| Addon | Page | Preset · View | Status |
|---|---|---|---|
| iam | Overview | DataPage · **DashboardView** + ListView | hand-rolled → adopt |
| iam | Users/Roles/Grants/Relationships | DataPage · ListView | partly hand-rolled (authored queries) → adopt¹ |
| iam | Connections | DataPage · ListView + FormView/DialogForm | ✓ D5 |
| iam | Schema (REBAC) | CanvasPage · **GraphView** + inspector | bespoke (keep; adopt GraphView shell) |
| iam | Login / OAuth callback | HeroPage | ✓ (PublicShell) |
| operator | Overview | DataPage · **DashboardView** | hand-rolled → adopt (daemon rows) |
| operator | Services/Workspaces/Sources/Jobs/Secrets/Templates | DataPage · **RowsListView** + actions | hand-rolled tables → adopt² |
| operator | (transport) | — | bespoke (daemon bridge; keep) |
| notes | Notes | DataPage · List/Board/Form + Chatter | ✓ D1–D6 |
| knowledge | Vault explorer | DataPage · **Explorer**(TreeView+List/Gallery) | View missing |
| knowledge | Note editor | DataPage · FormView(markdown) + **PreviewPane** | PreviewPane missing |
| knowledge | Graph | CanvasPage · **GraphView** | shell ok, wiring missing |
| knowledge | Canvas (JSON Canvas) | **CanvasPage** editor | wiring missing |
| storage | Files | DataPage · **Explorer** + **GalleryView** + **PreviewPane** | Views missing |
| storage | Upload | DialogForm | ✓ primitive exists |
| agents | Catalog (models/skills) | DataPage · ListView/**DashboardView** | adopt |
| agents | Instantiate wizard | DataPage · FormView **Notebook/Tab** | Notebook missing |
| agents | Chat | **CanvasPage** + chatter (ACP-WS) | wiring missing |
| agents | Activity/transcript | **TimelineView** | View missing |
| workflows | Editor | CanvasPage · GraphView | v1.1 |
| workflows | Runs | DataPage · BoardView/**GanttView/TimelineView** | v1.1 |

¹ iam adopt is gated by **schema availability**: only models the iam console
schema exposes as strawberry-django CRUD can use `useResourceList`/
`useResourceMutation`. Grants/relationships/rebac-schema are likely *custom*
GraphQL surfaces (verify per model — D5 did this for Connections); those stay
on authored queries fed into `RowsListView`, not full DataPage CRUD.

² operator adopt uses **RowsListView** (client rows from the daemon snapshot),
never `useResource*` — the daemon is a separate GraphQL server (bearer token,
own SDL). The wins are the shared section scaffold + RowsListView + DashboardView
metric grid + reusing EmptyState/LoadingPanel/ErrorBanner + statusBadge tones;
the transport stays bespoke.

## Full component lift (architect 2026-06-07: lift ALL p1 base components, then DRY operator)

Accumulate on `ui-refactor-dry`. Reconstruct to local conventions (no copy/
provenance); reuse local primitives; gate each (typecheck + vitest, render at
its adopter); commit own files only (storage/knowledge backends land in
parallel in the same tree). Source = `angee-django-p1/packages/base`.

| Component | p1 source | Status |
|---|---|---|
| DashboardView + Metric | views/DashboardView, views/dashboard/Metric | ✅ 37c4401 |
| Statusline slot (ConsoleShell) | shell StatusBar idea | ✅ 0b7583f |
| Tree + TreeView (+ FolderTree) | ui/tree, views/TreeView | ✅ abbe8bb (DnD seam deferred) |
| TimelineView | views/TimelineView (generalize local RevisionsTab/TimelineEntry) | ⏳ next |
| GalleryView | views/GalleryView | ⏳ |
| PreviewPane + file previewers | preview/PreviewPane, base-previews/* (Pdf/Image/Markdown/Code/Docx/Media/Html/Json) | ⏳ |
| CalendarView (+ calendar) | views/CalendarView, ui/calendar | ⏳ |
| GanttView | views/GanttView | ⏳ |
| Notebook + Tab + record Elements (Header/Title/StatusBar/SmartButton/Relation) | views/Notebook, layouts.md form Elements | ⏳ |
| Explorer (DataPage navigator slot) | ResourcePage navigator | ⏳ (after TreeView + PreviewPane) |
| ConsoleAppShell sub-nav derivation | shell/ConsoleAppShell | ⏳ (reconcile with TopMenu) |
| Chat (agents) | communication/Chat | ⏳ (later) |
| Drag seam (lib/dnd) | lib/dnd | ⏳ (unblocks TreeView DnD + Board) |

Then **DRY operator**: section scaffold + RowsListView + DashboardView +
fragments + statusBadge (operator stays on daemon rows, never useResource*).

## Build plan (sequenced by cross-addon leverage)

Each phase is a slice on the D1–D6 model: framework primitive first (in
`@angee/base`, headless data in `@angee/sdk`), then adopt it in the addons that
need it, gated by typecheck + vitest + reviewers + e2e. Layering stays
sdk→base→consumer ([[layering-sdk-ui-consumer]]).

- **P1 — DashboardView + Metric/Card Elements.** Generalize AggregatePanel +
  the auth-overview metric strip into a real DashboardView (Metric/Card
  Elements, tones, optional aggregate binding). Adopters: iam Overview,
  operator Overview. Highest leverage, smallest build.
- **P2 — operator section scaffold + RowsListView adoption.** One
  `OperatorSection` scaffold (title/loading/error/action-error) + port the 6
  daemon tables to RowsListView + DashboardView; reuse fragments + statusBadge.
  Pure consumer DRY, no backend risk (operator audit: ~150–200 LOC out).
- **P3 — iam page adoption.** Per-model schema-availability check; port
  Overview/Users/Roles/Grants/Relationships onto DashboardView/ListView/
  RowsListView; dissolve documents.ts authored queries where CRUD exists;
  delete identity-rows.ts row-shaping (computed columns). Keep SchemaPage +
  OAuthCallback bespoke.
- **P4 — TimelineView.** Generalize RevisionsTab/TimelineEntry into a
  TimelineView. Adopters: notes revisions, agents activity (when built).
- **P5 — TreeView + Explorer (DataPage `navigator`).** Tree primitive + the
  navigator slot. Adopters: storage folders, knowledge vault. (Foundational
  for the two biggest unbuilt addons.)
- **P6 — GalleryView + PreviewPane.** Card/thumbnail grid + preview Element/
  aside. Adopters: storage files, knowledge attachments, notes "Cards".
- **P7 — FormView Notebook/Tab + record-chrome Elements** (Header/Title/
  StatusBar/Relation). Adopters: agents wizard, multi-section records.
- **P8 — CanvasPage editor wiring** (graph/chat surfaces) and **CalendarView**;
  **GanttView** deferred to workflows v1.1.

P1–P4 harden the design system against the *shipped* addons (iam/operator/
notes) and are the immediate answer to "DRY iam and operator." P5–P8 build the
Views the *unbuilt* addons (storage/knowledge/agents) will need, so they're
adopters from day one instead of hand-rollers.

## Before building: a mockup

Per the architect: draft a mockup after this plan. The highest-value mockup is
a **console design-system overview** — one screen per archetype (Dashboard,
List, Form/record, Board, Gallery, Tree/Explorer, Graph, Timeline, Calendar,
Canvas/editor, Hero/login) in the shared shell, so the visual language and slot
contracts are agreed before the Views are built. Candidate tool: a Storybook
"design system" page or static HTML/Radix mock matching the existing
`radix-*.png` language (dark rail, metric strips, table density, semantic
badges).

## Open decisions (architect)

- Q1 — Mockup scope: one **all-archetypes overview** board, or a per-addon
  walkthrough (iam, operator, storage, knowledge, agents consoles)?
- Q2 — Mockup medium: Storybook page in `packages/storybook`, a static
  HTML/Tailwind mock, or annotated wireframes?
- Q3 — Build order: confirm P1→P8, or pull a specific unbuilt-addon surface
  (e.g. storage Explorer) forward because it's needed sooner.
- Q4 — Is this its own effort (separate from the D-series DRY), or do P2/P3
  (operator/iam adoption) run now as D7/D8 on `ui-refactor-dry`?

**Resume state:** plan drafted from p1 docs + iam/operator DRY audits +
existing radix mockups. Not started. Next: architect picks mockup scope/medium
(Q1/Q2), then draft the mockup, then sequence the build.
