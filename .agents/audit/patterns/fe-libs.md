# Frontend library-consistency inventory

Read-only audit of the FRONTEND of `integration-lift`. Source trees inventoried:
`packages/sdk/src`, `packages/base/src`, `examples/notes-angee/src/web`,
`examples/notes-angee/src/example/notes/web`, `src/angee/operator/web/src`,
`packages/storybook` (stories/`.storybook`). Tests, stories, and generated
codegen stubs excluded from the "production usage" counts unless noted.

CANON = `docs/stack.md` (Frontend + Rendered Binding tables). Line refs are
`file:line`. All paths are repo-relative to the workspace root.

The headline: the frontend is unusually disciplined. There is **no competing
library for any concern** — no axios/react-query/Apollo, no redux/zustand/jotai,
no react-hook-form/formik, no zod/yup, no dayjs/moment, no radix/headlessui/mui,
no sonner/react-hot-toast. The only real drift is (1) the URL-state boundary,
where nuqs and TanStack Router both write the same flat URL and the `tab` key
duplicates a data-view filter, and (2) a pile of stack.md rows that are declared
or listed but never imported.

---

## View library (React)
- canon (stack.md): React 19
- usage: sole view library — 178 production import sites across all trees
- verdict: CONSISTENT
- recommend: none.

## GraphQL client / data fetching
- canon (stack.md): urql React 5 + @urql/core 6 (client, normalized cache, subscriptions)
- usage: urql everywhere — `packages/sdk/src/graphql-provider.tsx`,
  `relay-invalidation.tsx`, `cache-config.ts`, `authored-hooks.ts`,
  `auth-hooks.ts`, `graphql-client.ts`, `resource-hooks.ts`, `document-query.ts`;
  operator at `src/angee/operator/web/src/data/operator-client.ts`,
  `data/transport.tsx` (8)
- note: the only bare `fetch` is `src/angee/operator/web/src/data/operator-client.ts:16`,
  inside a custom urql `fetch` option that injects a `Bearer` header — sanctioned
  glue, not an alternate data path. No axios / react-query / @apollo/client / swr
  anywhere.
- verdict: CONSISTENT
- recommend: none. urql owns all data fetching.

## GraphQL WebSocket lifecycle
- canon (stack.md): graphql-ws 6
- usage: `packages/sdk/src/graphql-client.ts:2` (`createClient as createWSClient`) (1)
- verdict: CONSISTENT
- recommend: none.

## Routing + route search params
- canon (stack.md): TanStack Router (route search) — owns "route search"
- usage: 13 production import sites; route search codec is a single flat codec —
  `createApp.tsx:163-164` wires `parseSearch: parseFlatSearch` /
  `stringifySearch: stringifyFlatSearch` (`createApp.tsx:192,202`). Data-view
  filter/sort/page/group/view live in Router search via
  `data-view-context.tsx:70` (`useSearch`) + `:73,93` (`useNavigate`).
- verdict: CONSISTENT (Router owns route search and the data-view URL state)
- recommend: none on its own — but see the URL-state boundary below.

## URL chrome query state (the boundary)  ← drift
- canon (stack.md): nuqs owns "remaining chrome query state such as top-menu tabs";
  TanStack Router owns route search; urql owns the normalized cache (not URL).
- usage (nuqs): `packages/base/src/chrome/TopMenu.tsx:4,76,95` — the top-menu
  `tab` literal (`all`/`starred`/`archive`) via `useQueryState` (1 real site).
  Adapter mounted at `createApp.tsx:18,243` (`NuqsAdapter` from
  `nuqs/adapters/tanstack-router`); storybook uses `NuqsTestingAdapter`.
- variant / overlap (Router): the SAME handler that sets the nuqs `tab`
  immediately writes a data-view filter into Router search —
  `TopMenu.tsx:94-97`:
  `void setActiveTab(tab.id); dataView?.setFilter(tab.filter);`. nuqs and Router
  both serialize into the **one** flat `URLSearchParams` (NuqsAdapter is the
  tanstack-router adapter sharing `parseFlatSearch`/`stringifyFlatSearch`), so
  the `tab` key (nuqs) coexists with `status`/`isStarred` filter keys (Router) in
  the same URL, written by two systems. `tab` is also **derived/duplicate state**:
  it restates which filter is active. urql holds no URL state — that half of the
  boundary is clean.
- verdict: DRIFTED (mild)
- recommend: pick ONE writer for the active-tab fact. Either (a) keep the tab in
  the data-view filter (Router) only and derive the active pill from the current
  filter — drop the nuqs `tab` write so the filter is the single source of truth;
  or (b) if a chrome-only tab key is genuinely wanted, have the data-view *read*
  it rather than having the handler write both. As-is, `tab` and the filter can
  desync (e.g. a filter set from elsewhere leaves a stale `tab=` in the URL). The
  Chatter inspector tab correctly uses local `useState`
  (`communication/chatter-context.tsx:72`), not URL — that boundary is right.

## Headless UI primitives
- canon (stack.md, Rendered Binding): @base-ui/react (dialog, popover, menu, tabs,
  tooltip, field, toolbar, scroll-area, …)
- usage: sole primitive source — ~30 `@base-ui/react/*` subpath imports across
  `packages/base/src/ui/**` (dialog x4, plus accordion, alert-dialog, avatar,
  button, checkbox, collapsible, context-menu, field, form, menu,
  navigation-menu, number-field, popover, radio, radio-group, scroll-area,
  select, separator, slider, switch, tabs, toast, toggle, toggle-group, toolbar,
  tooltip, use-render)
- note: no @radix-ui / @headlessui / @mui / @chakra / @mantine / antd anywhere.
- verdict: CONSISTENT
- recommend: none.

## Floating-element positioning  ← unused
- canon (stack.md, Rendered Binding): @floating-ui/react-dom (popover/menu anchoring)
- usage: ZERO imports in any source. Declared in `packages/base/package.json:31`.
  base-ui handles its own positioning (Positioner components); floating-ui is not
  wired.
- verdict: UNUSED (declared, never imported)
- recommend: either drop `@floating-ui/react-dom` from `packages/base` deps and
  delete the stack.md row, or document that it is an indirect dep of `@base-ui/react`
  and not a direct concern. Decide one.

## Styling — class merging
- canon (stack.md): tailwind-merge → `cn()` helper
- usage: single owner `packages/base/src/lib/cn.ts` (extends tailwind-merge with
  `ANGEE_TW_MERGE_CONFIG`); imported by 56 files via `../lib/cn`. No direct
  `tailwind-merge`/`twMerge` import outside `lib/cn.ts` and `lib/variants.ts`.
- verdict: CONSISTENT
- recommend: none.

## Styling — variant recipes
- canon (stack.md, Rendered Binding): tailwind-variants
- usage: single wrapper `packages/base/src/lib/variants.ts` (`createTV` sharing
  the SAME `ANGEE_TW_MERGE_CONFIG`); consumed by 68 files via `lib/variants`. No
  direct `from "tailwind-variants"` outside the wrapper.
- verdict: CONSISTENT
- recommend: none — exemplary: `cn` and `tv` share one merge config, so class
  precedence is identical across both paths.

## Styling — raw className / inline style
- canon (stack.md): `cn()` / `tv` own all class composition
- usage: NO raw `className={... + ...}` concatenation and NO interpolated
  `className={\`...${}\`}` template literals in production tsx. Inline `style={{}}`
  appears in 10 files but only for genuinely dynamic, non-tokenizable values:
  computed widths (`views/AggregatePanel.tsx:111`, `widgets/progressBar.tsx:112`),
  avatar background color (`ui/avatar.tsx:131`), fixed icon px (`chrome/AppRail.tsx:85`),
  etc.
- verdict: CONSISTENT
- recommend: none. Inline styles are legitimate dynamic-value escapes.

## Tailwind animations
- canon (stack.md, Rendered Binding): tw-animate-css
- usage: CSS `@import "tw-animate-css"` at `packages/base/src/styles/index.css:17`
  (no TS import — correct for a CSS utility pack). Declared `base/package.json:46`.
- verdict: CONSISTENT
- recommend: none.

## Icons — line/UI
- canon (stack.md): lucide-react ("name-referenced icon registry")
- usage: 14 import sites, funneled through one registry
  `packages/base/src/chrome/icon-registry.ts:40` (the only multi-icon import);
  components reference names via `Glyph`/`icon-registry`
  (`chrome/Glyph.tsx`, `ui/status-icon.tsx`, `createApp.tsx`). A few leaf
  components import a single glyph directly (`ui/dialog.tsx` X, `ui/checkbox.tsx`
  Check/Minus, `ui/pager.tsx`, `ui/select.tsx`, `ui/input.tsx`,
  `views/DataPage.tsx`, `chrome/AppRail.tsx`, plus
  `src/angee/operator/web/src/index.ts:2` Boxes).
- verdict: CONSISTENT
- recommend: none material. Optional: route the handful of direct leaf imports
  through the registry too for one ownership path, but direct single-glyph
  imports in primitives are reasonable.

## Icons — brand / vendor (the brand boundary)  ← unused
- canon (stack.md, Rendered Binding): simple-icons + @lobehub/icons (brand/vendor SVGs)
- usage: ZERO references anywhere, and declared in ZERO package.json. The only
  brand mark in use is `@angee/logo-react` (`auth/LoginPage.tsx:8`,
  `shell/PublicShell.tsx:2`), which is its own stack.md row and IS used.
- verdict: UNUSED (stack.md row with no declaration and no usage)
- recommend: the lucide (UI) vs brand/vendor (simple-icons/@lobehub) boundary
  cannot be violated because the brand side is not present. Either add the deps
  when a vendor-icon need lands, or drop the row until then. The `@angee/logo-react`
  boundary (brand lockup, not vendor icons) is respected.

## Tables (columns/sort/filter/group/select)
- canon (stack.md): TanStack Table
- usage: 4 sites — `packages/base/src/views/**` (`ListView`/`BoardView` bindings)
- verdict: CONSISTENT
- recommend: none. No ag-grid / react-data-grid.

## Virtualization
- canon (stack.md): TanStack Virtual
- usage: 2 sites in `packages/base/src/views/**`
- verdict: CONSISTENT
- recommend: none. No react-window / react-virtualized.

## Forms
- canon (stack.md): TanStack Form → `FormView` binding
- usage: 1 site `packages/base/src/views/FormView.tsx:2` (`useForm`, `useStore`)
- verdict: CONSISTENT
- recommend: none. No react-hook-form / formik / final-form.

## Schema validation  ← unused
- canon (stack.md): valibot ("server-emitted schema binding")
- usage: ZERO imports in any source. Declared in `packages/base/package.json:48`.
  No zod / yup / joi / ajv either.
- verdict: UNUSED (declared, never imported)
- recommend: drop `valibot` from base deps and the stack.md row until the
  server-emitted-schema binding actually exists, OR land the binding. Today the
  "server-emitted schema" concern has no code.

## Debounced inputs  ← unused
- canon (stack.md): use-debounce ("search and filter inputs")
- usage: ZERO imports in any source. Declared in `packages/base/package.json:47`.
- verdict: UNUSED (declared, never imported)
- recommend: drop the dep + row, or wire it into the search/filter inputs it was
  meant for (e.g. `ui/input.tsx`'s search field). Currently nothing debounces.

## i18n  ← effectively unused in framework source
- canon (stack.md): i18next ("per-addon namespace convention")
- usage: NO real `from "i18next"` import. `packages/sdk/src/i18n.ts` ships its own
  hand-rolled `interpolateMessage` / `translateWithFallback` and documents that
  "the host runtime owns the active i18next instance." i18next is an SDK
  peerDependency (`sdk/package.json:25`) but the framework source never imports it.
- verdict: UNUSED within this repo's source (host-provided by contract)
- recommend: keep the peerDependency (host owns the instance) but note in
  stack.md that i18next is host-resolved; the SDK only provides fallback helpers.
  Don't expect a direct i18next import in framework code.

## Markdown rendering
- canon (stack.md, Rendered Binding): react-markdown + remark-gfm
- usage: `packages/base/src/widgets/markdown.tsx` (both) (1 file each)
- verdict: CONSISTENT
- recommend: none.

## Text/Markdown editor
- canon (stack.md, Rendered Binding): CodeMirror 6
- usage: `codemirror` (1) + `@codemirror/{view,state,commands,lang-markdown}` (2+)
  in `packages/base/src/widgets/**`
- verdict: CONSISTENT
- recommend: none.

## Command menu (spotlight)
- canon (stack.md, Rendered Binding): cmdk
- usage: 2 sites (`packages/base/src/ui/command.tsx` + spotlight surface)
- verdict: CONSISTENT
- recommend: none.

## Calendar / date widgets
- canon (stack.md, Rendered Binding): react-day-picker
- usage: 1 site in `packages/base/src/**` date widget
- verdict: CONSISTENT
- recommend: none.

## Date formatting
- canon (stack.md): date-fns
- usage: 3 sites (date/relative-time widgets). No dayjs/moment/luxon.
- verdict: CONSISTENT
- recommend: none.

## Split panes
- canon (stack.md, Rendered Binding): react-resizable-panels
- usage: 2 sites (shell + inspector layouts)
- verdict: CONSISTENT
- recommend: none.

## Brand logo
- canon (stack.md, Rendered Binding): @angee/logo-react
- usage: 2 sites (`auth/LoginPage.tsx:8`, `shell/PublicShell.tsx:2`)
- verdict: CONSISTENT
- recommend: none.

## Drag and drop  ← unused
- canon (stack.md, Rendered Binding): @dnd-kit (board/rail interactions)
- usage: ZERO imports in any source. `@dnd-kit/core` + `@dnd-kit/sortable`
  declared in `packages/base/package.json:28-29`. The BoardView/Kanban exists
  (TanStack Table) but no drag wiring is present.
- verdict: UNUSED (declared, never imported)
- recommend: either wire @dnd-kit into the board/rail (the documented use), or
  drop both deps + the row until drag lands. Right now the "board interactions"
  concern has a dep but no behavior.

## Node/edge canvas  ← unused
- canon (stack.md, Rendered Binding): @xyflow/react (graph and canvas views)
- usage: ZERO references; declared in ZERO package.json.
- verdict: UNUSED (stack.md row, no dep, no code)
- recommend: drop the row until a graph/canvas view is built.

## File drop  ← unused
- canon (stack.md, Rendered Binding): react-dropzone (storage upload widgets)
- usage: ZERO references; declared in ZERO package.json.
- verdict: UNUSED (stack.md row, no dep, no code)
- recommend: drop the row until storage upload widgets exist.

## JSON / ANSI rendering  ← unused
- canon (stack.md, Rendered Binding): react-json-view-lite + ansi-to-react
  (debug and log panels)
- usage: ZERO references; declared in ZERO package.json. (The operator console
  is the obvious consumer for log/JSON panels but does not use these.)
- verdict: UNUSED (stack.md rows, no dep, no code)
- recommend: drop the rows until the debug/log panels are built, or wire them
  into the operator console where logs are rendered.

## UNSANCTIONED libraries (imported but NOT in stack.md)
- None. Every imported runtime/binding library maps to a stack.md row. The only
  non-stack imports are sanctioned tooling already in stack.md's Tooling/Testing
  sections (`@storybook/react-vite`, `@storybook/addon-themes`, `vitest`,
  `@testing-library/react`, `@playwright/test`, `@graphql-codegen/cli`, `vite`,
  `@vitejs/plugin-react`, `@tailwindcss/vite`, `graphql`).

---

## Top inconsistencies (worst first)

1. **URL-state boundary overlap (DRIFTED).** `chrome/TopMenu.tsx:94-97` writes the
   active tab to BOTH nuqs (`setActiveTab`, the `tab` key) and TanStack Router
   (`dataView.setFilter`, the filter keys) in one handler, into one shared flat
   URL. `tab` duplicates the active filter, so the two can desync (stale `tab=`
   after a filter set elsewhere). Fix: make the filter the single source of truth
   and derive the tab, or drop the dual write. This is the one place the
   nuqs/Router ownership line is genuinely blurred.

2. **Declared-but-unused base deps (4): `@dnd-kit/core`, `@dnd-kit/sortable`,
   `@floating-ui/react-dom`, `valibot`, `use-debounce`** (5 packages across 4
   concerns), all in `packages/base/package.json`, zero imports. They inflate the
   install and imply behavior (drag, validation, debounce, positioning) that does
   not exist. Fix: wire each into its documented use OR drop dep + stack.md row.

3. **stack.md Rendered-Binding rows with no dep and no code (6): `@xyflow/react`,
   `react-dropzone`, `react-json-view-lite`, `ansi-to-react`, `simple-icons`,
   `@lobehub/icons`.** These are aspirational rows — the canon lists owners for
   concerns (graph canvas, file drop, JSON/ANSI panels, vendor icons) that have
   no implementation yet. The brand/vendor-icon boundary (c) can't even be tested
   because the brand side is absent. Fix: move these to "Proposed, Not Locked" or
   drop until the surfaces ship, so stack.md reflects what's actually bound.

Everything else (React, urql, graphql-ws, Router route search, TanStack
Table/Virtual/Form, @base-ui primitives, cn/tv styling with a shared merge
config, lucide via icon-registry, date-fns, CodeMirror, cmdk, react-day-picker,
react-markdown, react-resizable-panels, @angee/logo-react, tw-animate-css) is
CONSISTENT with a single owning library and no competitor.
