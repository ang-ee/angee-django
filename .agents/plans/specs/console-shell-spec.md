# Console Shell Spec — region model + contribution seams

Status: **design, awaiting approval.** Grounds the next refactor of `ConsoleLayout`
(`packages/ui/src/layouts/ConsoleLayout.tsx`) and the convergence of the pages that
still nest their own shell. Decisions below are locked with the user; prior art is
cited so the conventions are inherited, not invented.

## Goal

One console shell owns *all* chrome. Every region is **optional but behaves
identically on every page** — pages only publish content into named slots; they
never re-render rail/topbar/breadcrumb/toolbar/status or nest their own multi-pane
shell. This is the DRY unlock: the 4 pages that currently nest `<Workbench>`
collapse onto the shell's panes (see "Current exceptions").

## Prior-art grounding

The model is the IDE-workbench consensus, with VS Code as the closest analogue
(deep-research, 23/25 claims confirmed against primary sources):

- **VS Code** — the canonical vocabulary: Activity Bar · Primary Side Bar (left
  explorer) · Secondary Side Bar (opposite, default Chat) · Editor (center) ·
  Panel (bottom) · Status Bar. Our `primary`/`secondary`/`content` names match
  1:1. <https://code.visualstudio.com/docs/getstarted/userinterface>
- **JetBrains IntelliJ** — tool-window *viewing modes* give the persistent-vs-
  transient taxonomy: Dock Pinned (push, always co-visible), Dock Unpinned
  (auto-hide), Undock (overlay, no scrim), Float (detached); every toggle lives on
  the edge **stripes**. This is the precedent for our drawers + their edge tabs.
  <https://www.jetbrains.com/help/idea/viewing-modes.html>
- **Material Design** — the push-vs-overlay + responsive rule: a *standard* drawer
  is co-planar and pushes content (expanded/large); a *modal* drawer overlays with
  a scrim (compact/medium); flip by breakpoint; below 320px swap to a nav bar.
  <https://m3.material.io/components/navigation-drawer/guidelines>
- **WAI-ARIA** — landmark regions + the window-splitter pattern for resize handles.
  <https://www.w3.org/WAI/ARIA/apg/practices/landmark-regions/> ·
  <https://www.w3.org/WAI/ARIA/apg/patterns/windowsplitter/>

Caveats recorded by the research (do **not** treat as current best practice):
Atlassian `@atlaskit/page-layout` is **deprecated** (→ `@atlaskit/navigation-system`)
and Shopify Polaris `Frame` is internal-only — both are useful legacy prior art for
slot vocabulary only. Material 3 "expressive" de-emphasizes the drawer for the nav
rail, but the standard/modal distinction is still documented.

## Region model

```
┌─────┬──────────────────────────────────────────────┐
│ App │ TopBar  (banner) ── pane toggles ───────────  │
│Rail ├──────────────────────────────────────────────┤
│(nav)│ Breadcrumb row                                │
│     ├──────────────────────────────────────────────┤
│     │ Toolbar row (ControlBand)                     │
│     ├──────────┬───────────────────┬────────────────┤
│     │ primary  │     content       │   secondary    │
│     │ explorer │   (main)          │  chatter +     │
│     │  (nav)   │                   │  page tabs     │
│     ├──────────┴───────────────────┴────────────────┤
│     │ Statusline row (contentinfo, optional)        │
└─────┴──────────────────────────────────────────────┘
  + DRAWERS: non-modal overlay panels pulled out by edge stripe-tabs
    (right + bottom), sticky across navigation, tabbed.
```

### Tier 1 — docked panes (the Workbench)

Push layout, resizable, collapsible, size-persistent. Owned by the `Workbench`
(`react-resizable-panels` via `page/SplitPanes`). **Per-page**: published on mount,
cleared on navigation.

| Region | Role | ARIA landmark |
|---|---|---|
| App rail | app switcher | `navigation` |
| Top bar | global chrome + pane toggles | `banner` |
| Breadcrumb | trail | `navigation` |
| Toolbar (`ControlBand`) | page action buttons | (toolbar, in-flow) |
| `primary` | left explorer/nav tree | `navigation` (it's a nav tree) |
| `content` | main preview/editor/list | `main` |
| `secondary` | inspector + Chatter (agent/comments + page tabs) | `complementary` |
| Statusline | status segments | `contentinfo` |

### Tier 2 — drawers (non-modal overlays)

**Non-modal** (no scrim — content stays usable, JetBrains "Undock"), pulled out by
**edge stripe-tabs** on the right + bottom edges, **sticky across navigation**
(mounted above the router outlet so a streaming log/chat survives route changes),
**tabbed** (a drawer hosts multiple panels). **Build-time composed** (addon
manifest), not page-scoped — for ambient tools like log viewers and chat sessions.
ARIA landmark `complementary`; non-modal ⇒ no focus trap, `Esc` is *not* required to
return focus (it doesn't block the page).

## Contribution seams (symmetric `use*` / portal)

| Slot | Seam | Status |
|---|---|---|
| Toolbar | `<ControlBand>` (portals into `area-control`) | exists |
| Statusline | `<Statusline>`/`StatusSegment` (portals into `area-status`) | exists |
| `primary` | `usePrimaryPane(node)` | **new** |
| `secondary` tabs | `useChatterContent({ tabs })` — change to **additive merge** with the default agent/comments/activity tabs | exists; tweak |
| Drawers | `useDrawer({ id, edge:'right'|'bottom', title, icon, content })` registered via the addon manifest | **new** |

`NotePage` (the example) is the **reference implementation** of the seam pattern
today — it already publishes `useChatterContent` tabs + a `<Statusline>`. The new
work generalizes that (adds `usePrimaryPane` + additive tabs + drawers).

## The three decisions — resolved + cited

- **(a) Bottom region span** — n/a; the docked tertiary was **dropped** ("drawers
  only for now"). The bottom is a drawer (overlay), naturally full-width. *(For the
  record, had we kept a docked bottom, VS Code's default is center-column / editor-
  area width, full-width as opt-in — <https://code.visualstudio.com/docs/configure/custom-layout>.)*
- **(b) Toggle placement** — docked-pane toggles (`primary`/`secondary`) stay in the
  **TopBar** (the existing `primaryController`/chatter toggles); **drawer** toggles
  are **edge stripe-tabs** on the right/bottom edges. Grounded: JetBrains puts every
  tool-window toggle on the edge stripes
  (<https://www.jetbrains.com/help/idea/tool-windows.html>); VS Code uses Activity
  Bar + title-bar layout buttons. Our hybrid (panes on the bar, drawers on stripes)
  is a deliberate, sourced choice.
- **(c) Drawer stacking** — **independent** (right + bottom open at once; multiple
  per edge = tabs). Reconciled with the research, which recommends *mutual
  exclusivity for **modal** (scrim) drawers only*: our drawers are **non-modal**
  (JetBrains Dock-Pinned/Undock are co-visible —
  <https://www.jetbrains.com/help/idea/viewing-modes.html>), so independent stacking
  is the correct, sourced behavior. If we ever add a **modal** drawer, it must be
  exclusive (single scrim) per Material — <https://m3.material.io/components/navigation-drawer/guidelines>.

## State persistence

Per the Atlaskit precedent (one shared UI-state store; per-slot opt-in size
persistence; collapse persists unconditionally): pane sizes persist through the
`Workbench` `autoSave` id (already the case); drawer open/collapsed + active-tab
state persists per drawer id; sizes via the same `SplitPanes`/localStorage path.

## Libraries

- **Docked panes** — keep `react-resizable-panels` (already owned via
  `SplitPanes`/`Workbench`); sufficient for a slot shell. `dockview` (the leading
  draggable-docking lib) is **rejected** — we don't want user-rearrangeable docking;
  it adds weight for affordances we don't use.
- **Drawers** — compose the existing Base UI `ui/drawer` for the side drawer; the
  bottom sheet can use Base UI or `vaul`. Decide at implementation (low-risk).
- *(Research note: the libraries/a11y angles returned no adversarially-confirmed
  claims; the choices above follow what the repo already owns + the WAI-ARIA spec,
  not the research.)*

## Current exceptions (audit of 72 console pages → 4 real targets)

Pages nesting their own `<Workbench>` (the convergence list):
1. `storage/StoragePage` (+ its in-content `SurfaceHeader`/toolbar → shell toolbar)
2. `knowledge/KnowledgePage`
3. `iam/SchemaPage`
4. `agents/AgentSessionsPage` (3 instances)

The other 67 pages are compliant (resource pages render only content). Two audit
"violations" are **false positives, leave them**: `NotePage`'s `<Statusline>` (the
correct seam) and `integrate/TemplatesPage`'s `<ControlBandProvider host={undefined}>`
(the documented create-dialog escape hatch).

## Phased implementation plan

1. **Phase 1 — pane harmonisation (the DRY unlock).** Add `usePrimaryPane`; make
   `Chatter` tabs additive; migrate the 4 pages off nested `<Workbench>` onto
   `usePrimaryPane` (explorer) + `useChatterContent` (details/backlinks tab) +
   `<ControlBand>` (toolbar), content = preview/editor only. Delete the nested
   workbenches + hand-rolled navigator/aside chrome.
2. **Phase 2 — drawer machinery.** `useDrawer` registry (manifest-composed), edge
   stripe-tab toggles, non-modal overlay rendering, sticky/tabbed, persistence.
   First adopters: a logs drawer + a chat-sessions drawer.
3. (Deferred) app-rail collapse; modal-drawer variant if a use case appears.

## Phase 2 — concrete design (drawers)

Non-modal, sticky, tabbed overlay panels pulled out by edge stripe-tabs (right +
bottom). Build-time composed; mounted by the shell above the page so a streaming
log survives navigation. Mirrors the existing contribution seams exactly (owner
map below).

**Contribution seam (mirror `slots`/`chatter`).**
- `addonManifest.drawers?: readonly DrawerContribution[]` with
  `DrawerContribution = { id: string; edge: "right" | "bottom"; title: string; icon?: string; sequence?: number; render: () => React.ReactNode }`
  (`packages/app/src/define-addon.ts` — add to `AddonManifest` + `ComposedAddons`).
- `composeAddons` merges via the ordered-list `mergeByKey` path keyed by
  `` `${edge}\0${id}` `` (edge-namespaced, like slots), sorted by `sequence`;
  collisions per edge+id fail fast.
- `AppRuntime.drawers` (`packages/ui/src/runtime/runtime.ts`) seeded by the
  composer; `useDrawers(edge?)` lookup mirrors `useSlot`/`usePreviews`.

**State (sticky, persisted).** A `DrawerProvider` in `@angee/ui` layouts holds, per
edge, the open drawer id (or `null` = closed) — mirrors `chatter-context`/
`primary-pane-context`. Persist per edge to `localStorage` (reuse `layoutStorage()`
from `page/SplitPanes`). Panel size persistence is a deferred follow-up (ship
open/closed first). Mounted in `ConsoleLayout` **above the `console-grid`** so the
drawer content components mount once at shell level and persist across route
changes.

**Chrome.** Edge stripe-tabs: a thin rail on the right edge (vertical labels, the
"Feedback"-tab look) and the bottom edge (horizontal), one tab per registered
drawer (icon + title), `aria-expanded`/`aria-controls`; clicking toggles+activates.
The panel is a **plain positioned overlay** (`fixed`, edge-anchored) using the
`drawerVariants` slide transforms — **NOT** the Base UI `ui/drawer` Dialog (no
scrim, no focus trap; it stays co-visible, JetBrains "Undock"). New `z-drawer: 40`
token (above topbar `30`, below `z-modal 100`). Panel host = `role="complementary"`
+ `aria-label`. Side + bottom independent (both open at once); multiple per edge =
internal tabs. `Esc` returns focus to the stripe-tab (not required — non-modal).

**First adopter (real content, low risk).** Operator **logs drawer**: the operator
manifest contributes `drawers: [{ id: "logs", edge: "bottom", title, icon, render }]`;
content composes the existing `useDaemonLogStream`/`LogPanel`
(`addons/angee/operator/web/src/views/sections/logs.tsx`) with a service/workspace
selector (the drawer isn't route-scoped, so it picks its target). This proves the
seam + survives navigation (the tail no longer resets on route change). The
agent-chat-in-drawer adopter is **deferred** (route-coupled ACP session, medium/high
risk — its own slice).

**Owner map.** seam → `define-addon.ts` (mirrors slots); registry → `runtime.ts`
(mirrors `useSlot`); state → a new `layouts/drawer-context.tsx` (mirrors
`primary-pane-context`); chrome → `ConsoleLayout` + a new `chrome/DrawerRail` +
`layouts/DrawerOverlay`; persistence → `layoutStorage()`; adopter → operator addon.

## Open questions

- ARIA: confirm `primary` explorer = `navigation` vs `complementary` (it's a nav
  tree → `navigation`); resize handles use `role="separator"` + `aria-valuenow`
  (window-splitter pattern) — `SplitPanes` should already do this; verify.
- Right-side coexistence: docked `secondary` (chatter, push) + a right **drawer**
  (overlay) both anchor right. They coexist (push vs float), but confirm the visual
  when both are open.
