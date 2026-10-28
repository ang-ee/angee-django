# Frontend Guidelines

Frontend code is TypeScript, React, and the rendered Angee experience. It owns
presentation, routes, menus, widgets, layouts, resource-view state, and
interaction.

Theme authors and host operators should also read the
[Appearance and theme addons guide](appearance.md). It defines the public token,
headless definition, build-default and preference boundaries.

Follow the shared development process and coding principles in the
[Development Guidelines](../guidelines.md) for every task; the rules below are the
frontend-specific layer applied during the Build step.

## Stack

The [opinionated stack](../stack.md) is the source of truth for frontend libraries
and what each one owns. Check it before adding a dependency or
hand-rolling a concern. TypeScript dependency setup belongs in `package.json`,
`pnpm-workspace.yaml`, and `pnpm-lock.yaml`.

## Package Layering

Framework package imports follow this DAG. Each arrow means "may import";
each package also uses its declared library dependencies.

```text
@angee/app ──→ @angee/ui ──→ @angee/refine
     │             └─────→ @angee/metadata
     ├───────────────────→ @angee/refine
     └───────────────────→ @angee/metadata
```

`refine` and `metadata` are independent leaves. Addons and the composed project
use these packages through declared dependencies; framework packages never
depend on addons or a composed project's generated schema.

| Owner | Responsibility and reference |
|---|---|
| [`@angee/refine`](../../packages/refine/README.md) | Transport, live providers, router binding and typed operations, with no domain or metadata knowledge |
| [`@angee/metadata`](../../packages/metadata/README.md) | Parse resource metadata and own its query, field, dimension and capability semantics |
| [`@angee/ui`](../../packages/ui/README.md) | Shared rendered surfaces and the runtime contracts those surfaces consume |
| [`@angee/app`](../../packages/app/README.md) | Compose addon declarations, routes, providers, registries and the application shell |
| Addon / composed project | Domain pages and schema-dependent generated documents |

Knowledge's role-scoped notes compose messaging's `RecordThreadStream` through
its child item and inline composer contracts. Knowledge owns page creation, body
writes, binding, and vault permission reads; record hosts declare the role and
public vault id. See the [knowledge addon](../../addons/angee/knowledge/README.md).

### Ownership boundaries

- Auth, preferences, and runtime i18n are app-owned providers under
  `@angee/app/src/providers/{auth,i18n}`. Transport auth headers stay in
  `@angee/refine`; chrome reads only the app-supplied session surface on
  `@angee/ui`'s `AppRuntime`.
- Dialect data hooks live in `@angee/refine` as metadata-free hooks. Callers
  resolve `resourceOperationTarget` at the metadata edge and pass the root as
  `{ root }`.
- The app owns the composed, active i18next instance. `@angee/ui` keeps an empty
  instance without resources only to resolve provider-less defaults. Addon
  bundles are namespace-relative, and the rendered binding namespace is `ui`.
  The shared `createAngeeI18nInstance` initializer in `@angee/ui/runtime`
  configures that instance and the provider-less binding's English defaults;
  i18next owns plural selection and interpolation in both cases.
- App copy belongs to manifest `vocabulary` scopes: an app (a root or an
  included app), optionally a route whose descendants inherit the override; a
  page composes every app on its trail, outermost first. The app validates existing
  message, model, field and menu keys; metadata projects presentation labels
  without changing query or model identity. Use host `createApp.i18n` for global
  copy, and scoped vocabulary for another addon's copy within an app. Native
  i18next scopes retain one locale across navigation. See the
  [manifest example](../../packages/app/README.md#app-vocabulary-and-shipped-views).
- Record display representation is a backend-emitted metadata fact in the
  `angee.resources` artifact. Frontend code reads that field and keeps only the
  `id` floor; it does not probe candidate display fields.
- Layering and unconsumed-owner guardrails are Vitest checks over the locked
  package graph. Do not add a boundary-lint dependency without updating
  [the opinionated stack](../stack.md) and the manifests together.
- **Guards.** The UX component ontology is executable: `packages/app/src/ux-ontology-guard.ts`
  (run by the architecture guardrails test over every addon and consumer root) fails on raw
  `<button>`/`<input>`/`<select>`/`<textarea>`/`<table>`/`<dialog>`/`<form>` elements, lucide
  icons rendered outside the manifest `icons` block, hand-spelled selected surfaces, deleted
  class names and raw pixel or control sizes, `toLocale*`/`Intl` formatters and literal
  date-fns patterns, imports of deleted owners, local `tv()` recipes beside a composed owner,
  and local status→tone maps. An exception is an allowlist entry with the file, the rule and
  the owner-level reason, never a workaround in the addon.

### One-way rules

These are the dependency invariants; a violation is a layering bug, not a
convenience.

- `@angee/refine` imports **only rented libs** — never `@angee/metadata`,
  `@angee/ui`, `@angee/app`, and never any `angee.resources` metadata.
- Import provider-adjacent Refine bindings (providers, dialect hooks, typed
  operation contracts) through `@angee/refine`; import ordinary framework hooks
  such as `useInvalidate` and `useList` directly from `@refinedev/core`. Do not
  widen `@angee/refine` into a general Refine re-export surface.
- `@angee/metadata` must **NOT** import `@angee/refine`.
- `@angee/ui` may import `@angee/refine` + `@angee/metadata`, but **not**
  `@angee/app`.
- `@angee/app` owns composition and `createApp`; lower framework packages do not
  import those concerns. The `AppRuntime` context and its lookup hooks live in **`@angee/ui`**
  (the binding owns the runtime it renders against); `@angee/app` composes the
  addon manifests (`composeAddons`) and mounts that `AppRuntimeProvider` with the
  merged value. `@angee/ui` reads it via the context — never by importing
  `@angee/app`.
- No addon imports deleted shell packages; addon web code composes the real
  framework owners directly.

### Native state owners

Client-side filtering, sorting, grouping, expansion, row selection, and pagination
compose TanStack Table row models. Angee keeps the thin lookup evaluator that lets
TanStack apply the URL-owned filter object to in-memory rows. Infinite message
history uses native Query pages with domain-owned
[window reads and retained-ID revalidation](upstream-reuse.md#history-retention).

### Reserved translation namespace

`ui` is reserved for the shared rendered binding. Addons contribute
namespace-relative messages under their own namespace; even an empty `ui`
bundle is rejected by [`composeAddons`](../../packages/app/src/define-addon.ts).
Use the owning addon's namespace with `createNamespaceT`; do not override
shared UI copy through an addon bundle.

## Component ontology

The [public catalog](../../packages/ui/src/catalog.ts) assigns one UX kind to
each shipped component and its owner-specific hooks. The
[Library overview](../../packages/storybook/src/stories/Library.stories.tsx) (story id `library-overview--default`)
renders the owners. The running application's `createApp(...).explain` exposes
menu and container provenance through the [runtime explanation owner](../../packages/app/src/explain.ts).

| Kind | Meaning |
|---|---|
| `token` | A named value or recipe: text role, tone, fill, radius, spacing, control height, elevation, motion, glyph, or a class recipe binding them. Each runtime export is counted once; there is no separate recipe category. |
| `primitive` | One interactive or layout element with its recipe, usually wrapping Base UI: Button, Chip, Tabs, Toolbar, Select, Input, Avatar, Tree, Pager, Dialog or Menu. |
| `fragment` | A data-free piece with fixed anatomy composed from primitives: PaneRow, PaneLabel, EmptyState, ErrorBanner, DefinitionPair, MetaGrid, status badge, RecordReference or RelativeTime. |
| `region` | A chrome composition owning a shell place: record header, command band, aside head, pane toolbar band, explorer pane, reader, drawer rail, top bar, breadcrumb or control band. |
| `surface` | A data-bound view: FormView, ListView, BoardView, Gantt, Dashboard, TreeView, Chatter or its panes. |
| `page` | A shared design-system shape composing regions and surfaces: record, explorer, console, settings or hero page. An addon's routed page is a route instance that composes this shape. |
| `contribution` | What addons add through containers: verbs, chrome controls, aside tabs, drill-ins, related groups, timeline kinds, widgets, sections or columns. |

**The design system owns tokens, primitives, fragments, regions and pages.**
Addons own contributions and surface content. An addon needing a missing owner
first identifies the shell place or shared anatomy, searches the existing
catalog and container contracts, and brings the concrete gap and intended
composition to the framework owner. Extend that owner or contribute through its
container. A new owner belongs in `@angee/ui` (or app composition in
`@angee/app`) with its story and catalog entry, never as an addon-local copy.

**The shipped owner is the only Storybook authority.** Authoritative stories
render real `@angee/ui`, `@angee/app` shell owners and shipped addon components,
with fixture data and providers. They never invent substitute anatomy. A study
needing its own markup belongs under `Explorations/<YYYY-MM-DD> <name>` (a
`packages/storybook/src/explorations/` folder with its own story glob in
`.storybook/main.mjs`; none exists today), names the ruling it fed and its
superseding owner, and is deleted once that owner exists. Explorations sort last
and never enter the Library.

The `@angee/ui/catalog` subpath audits runtime exports, including aliases; the
root entry does not load it. Type-only exports are contracts, not rendered
components. Declarations own `@kind` and the closed `@region` shell place;
`@owner` lets hooks, parts and values follow their rendered owner. Nonvisual
transport, schema, runtime and provider infrastructure needs an explicit
`@reason` for `kind: null`. Upstream re-exports remain attributed to the local
re-exporting file. `@deprecated` marks deletion candidates; other exports are
stable.

The [generator](../../packages/storybook/scripts/catalog.mjs) reads these facts
through TypeScript, reports independent owners of the same shell place, and
derives story links only from CSF `meta.component` or `parameters.owner`.
Missing coverage stays `null` and is visible in the Library.
`recipeAxes(recipe)` reads native tailwind-variants metadata for specimens.
The guard regenerates and diffs the catalog, so shape-valid but stale facts fail.

Token owners are visible in the live sheets:
[type roles](../../packages/storybook/src/stories/TypeRoles.stories.tsx) (story id `foundations-type-roles--default`),
[tones and fills](../../packages/storybook/src/stories/TonesAndFills.stories.tsx) (story id `foundations-tones-and-fills--default`),
[geometry](../../packages/storybook/src/stories/GeometryTokens.stories.tsx) (story id `foundations-radius-spacing-and-control-heights--default`)
and [glyphs](../../packages/storybook/src/stories/Glyphs.stories.tsx) (story id `foundations-glyphs--default`).
The unused tokens and unresolved classes the inventory found were deleted or
repaired at their owners on 2026-10-20; the “no owner” lists stay empty.

## Rules

- Python ships schema and operations. TypeScript ships UX.
- **Schema dependence stops at the composition boundary.** `@angee/refine`,
  `@angee/metadata`, `@angee/ui`, and `@angee/app` stay schema-independent.
  Framework-owned operations use typed documents against their owned contract;
  project-specific operations arrive through explicit composition inputs.
  Only addon web packages and the composed project import generated documents
  from the host's `@angee/gql`. See the executable
  [architecture guardrails](../../packages/app/src/architecture-guardrails.test.ts).
- **Generated authored operations are typed, never hand-mirrored.** In a
  schema-dependent package, a bespoke (non-CRUD) operation is a `graphql()`
  document imported from `@angee/gql/<schema>`; its
  result/variables types come from the generated `TypedDocumentNode` (use
  `DocumentType<typeof Doc>` for named result types and
  `DocumentVariables<typeof Doc>` from `@angee/refine` for named variable types) —
  never a hand-written `…Data`/`…Variables` interface, and never call-site
  `<TData,TVars>` generics on the `useAuthored*` hooks. The operation's **file
  name picks its schema**: `documents.ts`/`documents.console.ts` → console,
  `documents.public.ts` → public. An op must live in a `documents*.ts` file (the
  codegen glob does not scan inline ops), and a console op placed in a
  `documents.public.ts` (or vice versa) fails codegen loudly against the wrong
  schema. Optional schema contributions keep their documents and UI registrations
  in the contributing addon's web package, so hosts without it never scan those
  operations; see [OIDC sign-in](../../addons/angee/iam_integrate_oidc/web/src/index.ts).
  An opaque `JSON`-scalar value is parsed at its domain owner with a
  valibot schema (`safeParse`), never asserted into an application shape; a
  recursive shape a declarative schema cannot express may wrap its type guard
  in `v.custom`, keeping the parse boundary in the schema.
- **Eligible ActionResult mutations are derived, not authored.** Codegen in
  `packages/app/bin/angee-web-codegen.mjs` derives mutations with at least one
  argument when every argument is required (non-null without a default), or
  when `id: ID!` is the sole required argument and all others are optional or
  defaulted. Other shapes stay authored. Generated documents preserve the
  schema's argument types and defaults. Call
  `useActionMutation<ActionFieldName>("field")` from `@angee/ui` in headless
  rendered-view code, or
  `useRecordActionMutation<ActionFieldName>("field")` for a rendered
  `@angee/ui` `<Action run={...}>` bound to the open record. `ActionFieldName`
  comes from `@angee/gql/<schema>/actions`; no document, result type, or
  variables are authored. The shared hook owns deriving the Hasura custom
  mutation and running it through refine `useCustomMutation`; the rendered record
  adapter owns binding it to `ActionContext` (record id, refresh,
  missing-record handling, success hooks). Set `idArgument` to the schema
  argument that receives the record id when it is named differently (for example,
  `"round"`); `null` on an outcome mutation sends only its explicit arguments.
  Don't hand-author these as `graphql()` documents or page-local
  `ctx.record.id → mutate → refresh` callbacks.
- **A contributed record verb that needs input declares `args`.** A
  `ConditionalMutationButton` (`@angee/integrate`) with `args` opens the shared
  `ActionFormDialog` and fires the generated action through
  `useRecordChromeActionOutcome`, so the dialog binds the outcome's in-band
  `validationErrors` and toasts on `ok`; never pair a settled
  `useRecordChromeActionMutation` with a hand-rolled dialog (double toast, lost
  field errors). A channel vendor contributes such a verb through its
  `defineChannel*BridgeAddon` `recordActions`, scoped to its own rows.
- **`ActionResult` verbs settle through `useActionResultRun`** (`@angee/ui`) —
  it fires the verb, toasts the outcome (danger with the in-band non-field
  reasons; success with the message), and deep-links to a created record via
  `linkTo` + the routed resource page when the outcome carries an `id`. It
  serves authored verbs (extract the outcome field inside `fire`) and derived
  verbs (`useActionMutation`'s mutate resolves the full `ActionOutcome`)
  alike; never hand-roll the fire → toast → navigate ceremony in a chrome.
- React does not own business logic, permissions, models, or persistence.
- Dashboard widget visibility is declared separately from its data filter.
  [`WidgetSpec.visibility`](../../packages/ui/src/dashboard/headless.ts) names
  the resource scope that authorizes listing; the dashboards backend answers
  for the current actor before the UI mounts queries and packs visible widgets.
  An authorized empty result remains visible. Built-in query widgets put Refresh
  in their options menu; authored widgets own their refresh controls.
- **React state has one owner.** Keep canonical facts in the smallest owner:
  route/search facts in TanStack Router/nuqs, server facts in Refine core reads
  and TanStack Query, native controlled table state in `ResourceViewProvider`,
  form values/baselines/errors in React Hook Form through `FormView`, and ephemeral interaction state in the
  component that handles it. Lift state when siblings coordinate; do not keep
  parallel local copies. Resource-view selection and its observed row snapshots
  live together in that provider, surviving pagination, page-size changes,
  surface remounts and view switches that keep the same grouping. Switching views
  can install a new default grouping, which clears selection. Only one scope
  holds selection: entering another inherited scope or selecting at the owner
  clears the previous scope. Collection changes clear them before children
  render; returning to an inherited base-filter scope after entering another
  never revives its old selection. See the
  [selection and bulk-action contracts](../../packages/ui/src/views/resource/resource-view-context.tsx)
  and [list declarations](../../packages/ui/src/views/resource/resource-view-types.ts).
- **Derive during render.** Do not store `filteredRows`, selected records,
  labels, options, variables, column lists, or capability booleans in state when
  they can be computed from props, route search, GraphQL results, model metadata,
  or existing Angee state. Use `const` first and `useMemo` only for expensive work
  or referential stability; never use `useEffect` + `setState` to mirror render
  data.
- **Effects are for external synchronization.** Use `useEffect` to sync with
  browser APIs, storage, subscriptions, timers, navigation after async data, or
  imperative libraries like CodeMirror. Event logic belongs in handlers, and
  render-derived data belongs in render.
- Use `defineAddon` for headless addon composition, `defineBaseAddon` for
  rendered addon composition, and `createApp` for the project's host
  composition. One greppable seam per addon — never annotate a bare
  `const x: BaseAddon = {…}`. These contracts and the packages that own them are
  described under [Package Layering](#package-layering).
- **An app root declares itself; the deployment selects the app.** A
  top-level menu root is an app. It may declare `home` (a route name inside
  the root), `brand` (`{ name, mark }`, with `mark` a registered glyph; it
  defaults to the root's label and icon), `theme` (an installed theme id), and
  `railExpanded` (the desktop rail's default expansion).
  They layer like any menu field, so a dependent addon or `ANGEE_UI.menus`
  overrides them, and an included app drops them. The deployment's
  `ANGEE_UI.shell` selects what the console shows; products never name
  hostnames, and hosts pass no product facts:

  ```python
  ANGEE_UI = {"shell": {
      "brand": {"name": "Suite", "mark": "suite"},  # nothing selected: every root
      "theme": "suite.light",
      "apps": {"payables": {"rail": ["accounting", "files"], "home": "accounting.bills"}},
      "hosts": {"payables.example.com": "payables", "work.example.com": "pm"},
  }}
  ```

  [`selectApp`](../../packages/app/src/boot-app.tsx) takes `?app=<root id |
  apps name>`, else the hostname's `hosts` entry, else nothing; no choice is
  stored. A root id is the one-root app with the root's home, brand, theme and rail default.
  A named app takes its own, else its first rail root's; an unset theme falls
  back to `shell.theme`. Nothing selected shows every root with `shell.brand`
  and `shell.theme`. An unknown `?app=` warns in development and falls back.
  `createApp` checks every root, app and host at boot, selected or not: a
  rail lists top-level roots, an app name is no root id, a home lies inside
  its rail and stays available, a declared mark is registered and a theme
  installed. An addon removing the page a root's home names moves that home.
- **A selection shapes navigation, never access.** The rail, command palette
  and Refine resources follow the selected rail plus the personal Settings
  roots. A page outside the rail stays reachable by URL; its words, record links
  and `when: { app }` are those of the selection's home root. No route is
  guarded: only a menu `remove` makes a page unavailable. Rail, login, public
  mark and document title read the selected brand through `useRuntimeBrand`;
  shell components hard-code no identity. The selected theme is the appearance
  default, and the person's own choice still wins. The rail likewise takes its
  selected app's `railExpanded` default only when no expansion preference is saved;
  undeclared, it starts expanded at desktop widths. Visiting a route without
  selecting an app does not apply that app's rail default.
  `/` signs in like the `console` layout, then lands by [`landingTarget`](../../packages/ui/src/chrome/app-rail-model.ts).
  A selected app lands on its home, else on its rail's first app. Otherwise
  `/` lands on the person's saved home page, their rail default, the first app
  of their ordered rail (top-level `sequence` orders it by default), then
  Settings. Never add a landing flag or fall back to route order.
- Rendered resource pages use `resourcePageRoutes(name, path, component,
  resource?)` from `@angee/app`; the helper owns the list + `$id` child pair and
  the default `"console"` layout. An explicit `detailComponent` gets a native
  router index page for the list, so its record page replaces the list. Without
  it the list surface keeps owning the selected record through the child param.
  Addon manifest tests call
  `expectValidBaseAddon(manifest)` from `@angee/app/testing` and keep only
  genuinely addon-specific assertions.
- Route declarations are the only place a URL path is spelled. Menus name their
  target with `route:`; components resolve addon-local routes with
  `useRouteHref`, and resource-backed links use the runtime resource lookup.
  Cross-addon render/event links use the non-throwing route probe and disappear
  or disable when their dependency is not composed; full app composition still
  fails fast on invalid declarations. Keep query-string codecs addon-local.
  `resourcePageRoutes` names record children `${collectionName}.record` by
  default; use `detailName` only when preserving a deliberate established name.
- In-app anchors compose the [in-app link owner](../../packages/ui/src/lib/in-app-link.tsx),
  mounted by `createApp`; plain clicks route automatically, hover, focus and touch
  preload the route under the router's `defaultPreload: "intent"`, and
  provider-less links stay native. Never pass `onNavigate` merely to call the
  router, or put a query-bearing href in TanStack `to`; use `navigate({ href })`,
  the owner's `hrefLocation` (also for a redirect, which older routers preload
  only from location options), or the
  [chrome href conversion](../../packages/ui/src/chrome/href-link-options.ts).
  Use `rel="external"` for server-served root-relative paths such as admin,
  media and logout so they load as documents.
- [Breadcrumb history](../../packages/ui/src/chrome/Breadcrumb.tsx) lives in TanStack
  location `state.breadcrumbTrail`, validated at the breadcrumb owner: console
  content links, including drawers and programmatic record navigation, carry
  the current nested trail. Revisiting a pathname (even with another query) or
  activating an earlier crumb truncates it; only the eight most recent entries
  remain. Same-location search updates pass `state: true`, browser Back restores
  history, and chrome navigation starts fresh. Menu destinations show no strip.
- Compose addon capabilities at build time through the manifest + `composeAddons`
  (widgets, i18n, icons, forms, containers, previews, and menu declarations); never
  register or mutate a module-global at runtime. `usePreviews`/`useWidget`/
  `useContainer` read the composed `AppRuntime`; menu declarations project into refine
  resources and chrome renders refine `useMenu`.
- **Custom resource widget keys use `namespace.addon.widgetName`.** Keep namespace
  segments lowercase (digits and underscores are allowed); use camelCase for a
  multiword terminal widget name, matching the web widget registry. Backend field
  metadata and the owning addon's `widgets` contribution use the identical key,
  for example `angee.integrate.integrationSyncCursor`. The validator in
  [`angee.graphql.data.resource_fields`](../../addons/angee/graphql/data/resource_fields.py)
  also accepts existing underscore names; unknown bare names remain errors.
- **A resource registry key is the emitted canonical `modelLabel`** (for example
  `"integrate.OAuthClient"`). Addon composition may accept a unique bare or
  lowercase spelling only because `createApp` canonicalizes it fail-fast against
  the merged schema inventory; runtime render lookups degrade with a development
  warning when a spelling is unavailable in the active schema. Durable manifests,
  authored-operation labels, routes, forms, and runtime maps store the canonical
  qualified label.
- Shell-published surfaces (`usePrimaryPane`/`PrimaryPanePublisher`,
  `useChatterContent`) are effect publishers. Publish memoized nodes/content,
  and keep any callbacks they close over stable; when a callback wraps
  Refine/query mutation objects that may refresh identity, expose a stable
  callback that reads the latest execution context from a ref (`useLatestRef`).
  Explorer pages compose `ScopedExplorerPane`, which owns the primary-pane
  navigator publication plus the root loading/empty gate; addons provide row
  projection, route transitions, DnD policy, and domain actions. Context another
  addon owns beside a page's list is that addon's pane component, published with
  `usePrimaryPane` rather than stacked as a second list. The left pane never
  describes the record (see the shell's regions below). Page tests use
  `ShellPageTestProviders`, `PrimaryPaneTestHost`, and `ChatterTabsTestHost`
  from `@angee/app/testing` instead of hand-rolled shell provider wrappers.
- **The shell has four regions, each with a test.** The left pane is the
  explorer: trees, collection lists in split placement, session rails, smart
  views and the record pager; it changes which record is in the middle and
  never says anything about the record. The middle is the subject: the form,
  its body sections, the statusbar and the primary toolbar. The right pane is
  the context aside: what is around the record (its Timeline, Discussion,
  Tasks, Related), a page's selection inspector, and drill-ins; it may act on
  the record's process (answer, stop, revert, comment) or lead to a related
  record, and it is never a second middle: on a record page it never edits the
  record's content (a studio page's selection inspector edits the selection,
  because there the canvas is the subject and the inspector its editor). The
  drawers are tools: session-scoped and record-independent (agents, logs). A
  candidate that fails its region's test belongs elsewhere. Related records
  navigate the middle and the explorer keeps the place; the aside drills in for
  a bounded question about one object in the record's context (this run, this
  changeset, this decision, this conversation) and links to the object's page
  for the open-ended work. Aside publishers compose by owner for their mounted lifetime:
  panels mount when first visited and stay mounted for that record, so draft
  input survives switching tabs. Consumers do not mount their own aside. Its
  tabs are `record#aside` and `<model>#aside` children, narrowed by the layers
  for the current app and route before tabs mount (see
  [Context aside](#context-aside)); a container nobody narrows keeps all its
  children, including under a selected app. Public and sign-in routes belong
  to no app. A `record#aside` child appears only on model-backed record views;
  a route whose param names no model has no aside. See the
  [Chatter owner](../../packages/ui/src/communication/Chatter.tsx).
  Shipped examples: [command band](../../packages/storybook/src/stories/CommandBand.stories.tsx) (story id `page-commandband--default`),
  [record header](../../packages/storybook/src/stories/RecordHeader.stories.tsx) (story id `fragments-recordheader--default`),
  [context aside](../../packages/storybook/src/stories/Chatter.stories.tsx) (story id `communication-chatter--default`),
  [explorer pane](../../packages/storybook/src/stories/ScopedExplorerPane.stories.tsx) (story id `views-scopedexplorerpane--pane`),
  and [tool drawers](../../packages/storybook/src/stories/OwnerSpecimens.stories.tsx) (story id `library-owners--drawers`).
- Human-in-the-loop queues use the resource page shell for filtering, grouping,
  paging, record selection, and URL state.
- **Routed page components are code-split.** In an addon manifest give each
  routed page `component: lazyRouteComponent(() => import("./views/Page"),
  "Page")` (the stack-native helper from `@tanstack/react-router`, already a
  direct addon dep) — never an eager `import { Page }` + `component: Page`, which
  pulls every page into the entry graph. The router owns the route-loading
  fallback *once*: `createApp` sets `defaultPendingComponent` (the page-shaped
  `LoadingPanel shape="page"`), which wraps every non-root match in Suspense
  inside its layout's `<Outlet/>`, so the chrome stays mounted. The previous page
  stays mounted but hidden meanwhile; its portaled control band and statusline
  hide with it ([layout band](../../packages/ui/src/layouts/layout-band.tsx)).
  Link intent preloads the chunk, so the fallback shows only for slow loads. Do not hand-roll
  `React.lazy` + a manual `<Suspense>`
  around a route's `<Outlet/>`. Lighter manifest content (container children,
  forms, glyphs) stays eager. Keep a registered form and its descriptor in an
  eager form module when its routed page is lazy; the manifest must not import or
  re-export the page module. A page may import its form, and form callers import
  the form module directly. A heavy optional surface may use `React.lazy` inside
  the shared `LazyBoundary` when its dependency tree otherwise enters the boot
  bundle. The [agents chat](../../addons/angee/agents/web/src/views/AgentChatterPane.tsx)
  defers assistant-ui, streamdown and its code renderer until chat opens; its
  `record#aside` child and context remain eager so addon composition stays
  synchronous. A transport or context shared
  by pages and shell contributions declares
  `layoutProviders` on `defineBaseAddon`, keyed by layout and contribution id.
  The layout mounts these providers once inside its authenticated schema context,
  above chrome and routed content; page and drawer wrappers are unnecessary.
- One component tree. Extend or register; do not fork.
- **Containers are additive extension points.** Add a child before copying a
  component; [Containers](#containers) owns the rules. Console-wide notices are
  `shell#notices` children; `ConsoleLayout` renders them below navigation and
  above page controls. The contributing addon owns visibility, permissions,
  status, and actions, and composes the existing `Banner` surface.
  A child id is namespaced by its addon and declared once per address; a second
  declaration **fails** at composition — it is never a silent override decided
  by addon array order. To vary a child per row, key it on the fact the row
  already carries (an `ImplClassField` value, through `impl` or `variant`), never
  on a probe of the record inside the component: a child that inspects the row
  to decide whether it should have rendered is declared in the wrong place.
  **Record verbs resolve kind → canonical model → concrete model**: a child of
  `form#actions` shows on every form, one of an MTI parent's `#actions` on each
  subtype, and a `variant` stands in for its original on one implementation's
  rows only. That is how the addon owning an MTI parent contributes a verb once
  for every subtype, and how two vendors specialize the same verb on one model
  without colliding. Removing another addon's verb from a model instead
  displaces it for every row of that model and caps the model at one vendor.
- Tokens beat color props and one-off variants. Theme by overriding tokens.
- **Typography composes named text roles.**
  [`textRoleVariants`](../../packages/ui/src/ui/text.tsx) owns the shared
  display/title/heading/value and secondary-text typography; callers add only
  layout. A two-tone heading repeats the same role with `tone: "muted"`, while
  a cell that needs only aligned figures uses the `numeric` modifier.
- **Definition pairs have one owner.**
  [`DefinitionPair`](../../packages/ui/src/fragments/DefinitionPair.tsx) owns one
  label, value, optional muted detail, and optional action. Collections keep
  their own structure: `MetaGrid` owns its shared label column, while metric
  tiles own their cards, links, and icons.
- Implementation inspection reuses field-owned choice metadata. Platform owns
  the registered-type catalogue and source viewer; configured records and domain
  contracts stay with their addon. Render declared configuration with the
  existing FormSpec descriptors, and keep absent defaults distinct from explicit
  `false`, zero, or empty values.
- Color is two orthogonal axes (`lib/tones.ts` is the owner): `tone` (the palette
  — `neutral`/`muted`/`brand`/`accent`/`info`/`success`/`warning`/`danger`/`purple`/`pink`)
  × `variant`/fill (`solid`/`soft`/`surface`/`outline`/`ghost`). `muted` is the quiet
  tone for inert and not-yet states (planned steps, idle rows); no component keeps a
  private muted treatment. Drive recipe color through `toneClass(tone, fill)`; never
  hand-type a soft/solid tone triple, and never use the retired `default`/`error`
  names (they are `neutral`/`danger`).
- **One pill: `Chip`** (`ui/chip.tsx`): tone × fill × size (`micro`/`sm`/`md`) ×
  shape, a status `mark` (`dot`/`icon`) and a `pressed` toggle mode that renders a
  `<button aria-pressed>` (scope chips, reactions, type filters). `CountBadge`
  (`ui/count-badge.tsx`) is the one numeric count pill (tab counts, overlay counts on
  icon buttons and app tiles, tree counts) and formats through `formatNumber`.
  `Badge` and `Tag` no longer exist. A status value renders through `StatusBadge`
  (`widgets/statusBadge.tsx`), never a local label or tone map. Owner stories:
  [pills](../../packages/storybook/src/stories/Chip.stories.tsx) (story id `primitives-chip--playground`) and
  [count pills](../../packages/storybook/src/stories/CountBadge.stories.tsx) (story id `primitives-countbadge--default`).
- **One toolbar: `Toolbar`** (`ui/toolbar.tsx`, Base UI, roving focus) is the root of
  every bar: `ControlBand` and so `CommandBand`, the `PaneToolbar` band, the full
  `ResourceToolbar`, `PageToolbar`, `ChatBar`. `Toolbar.Button` is the one `Button`
  recipe registered with the bar; a bar never spells its own row or button chrome.
  Owner stories: [command band](../../packages/storybook/src/stories/CommandBand.stories.tsx) (story id `page-commandband--default`),
  [control band](../../packages/storybook/src/stories/ControlBand.stories.tsx) (story id `page-controlband--default`) and
  [toolbars](../../packages/storybook/src/stories/OwnerSpecimens.stories.tsx) (story id `library-owners--toolbars`).
- **One icon button: `Button` at `iconSm`/`iconMd`/`iconLg`** (a ghost `iconSm`, 28px,
  is every pane, band and header control) with `pressed` (`aria-pressed` + the selected
  treatment) and `badge` (a `CountBadge` on the corner: needs you, unread). The three
  `form#chrome` controls, pane toolbar descriptors, the aside search toggle and the
  dashboard widget controls all render it; `PaneToolbarIconButton`, `ChatHeaderAction`
  and the `icon` button variant no longer exist. `SelectionBarAction` remains the one
  wrapper, because the selection bar's surface recolours the button. Owner story:
  [icon controls](../../packages/storybook/src/stories/Button.stories.tsx) (story id `primitives-button--icon-controls`).
- **One verb menu: `ActionMenu` + `ActionTrigger`** (`toolbars/ActionMenu.tsx`) behind
  every `⋮`: the record band, row menus, `PaneToolbarMenu` (descriptor items and
  radio groups over the same owner), the visible-fields menu, the dashboard widget
  menu, the collapsed statusbar. Field choices inside such a menu stay native items;
  verbs are `ActionTrigger`s. Every fire → toast flow settles through
  `useActionResultRun`; row verbs included. Owner story:
  [action menu](../../packages/storybook/src/stories/ActionMenu.stories.tsx) (story id `toolbars-actionmenu--default`).
- **Time is absolute; relative time is a live header's.** Every list, row, facts
  line and period title formats through `date-format.ts`: `formatDate`,
  `formatDateTime`, `formatTime` (a row whose day label already names the day),
  `formatDayLabel` (Today, Yesterday, a weekday, then the date — every day
  separator: `PaneLabel` in `TimelineRail`, `MessageDaySeparator`, list date
  groups) and `formatMonth`. A row or facts line renders its stamp through the
  `AbsoluteTime` fragment (the clock time, the day or both, with the full stamp
  as the tooltip); `MessageRow` and `StepList` compose it. `RelativeTime` /
  `formatRelativeTime` belong only to
  a header whose value keeps moving while the page is open (a chat, a running
  job). Never `toLocale*`, never a date-fns `format` with a literal pattern,
  never a raw ISO string as user text. Numbers and counts format through
  `formatNumber`; JSON through `formatJson` (`widgets/json-format.ts`).
- **One option list with a query: `Combobox`** (`ui/combobox.tsx`, Base UI) — a
  `field`-shaped trigger or a `Chips` row with an inline `Input` and a `caret`
  trigger, one `Popup` on the control layer, `Search` as the popup head, and a
  `List`/`Item`/`Empty` sharing the `Select` recipe. The `combobox` and
  `many2many` widgets and the resource `SearchBox` compose it; `Command` (cmdk)
  stays the palette's engine. `SearchInput` is the one standalone search field
  (toolbars, pickers, the app chooser). Owner story:
  [combobox](../../packages/storybook/src/stories/Combobox.stories.tsx) (story id `primitives-combobox--default`).
- **One label: `FieldLabel`** (`ui/field.tsx`), `standard` in dialogs and
  settings, `variant="eyebrow"` on the form sheet, with `meta` for a compact
  note at its end; `FieldRoot` takes `span`/`area` to sit in a `FormGrid`.
  `RequiredMark` and `OptionalHint` live beside it. `Label` and `FieldRow` no
  longer exist. Owner story:
  [field label](../../packages/storybook/src/stories/FieldLabel.stories.tsx) (story id `primitives-fieldlabel--default`).
- **One indentation: `depthIndent`** (`ui/tree.tsx`) computes every nested row's
  inset (`Tree`, grouped lists, the gantt sidebar); `DeletePreviewTree` is a
  `Tree` over the delete preview. **One prose: `Markdown`**
  (`widgets/markdown-prose.tsx`) renders every read-only markdown surface, the
  file preview included. `Avatar` renders every initials circle (`userRef`,
  `ownerCell`). `TimelineView`, `TimelineEntry`, `Divider`, `TextInput`,
  `DebouncedSearchInput`, `PopoverInput` and `ToolbarInput` no longer exist.
  Owner stories: [avatar stack](../../packages/storybook/src/stories/Avatar.stories.tsx) (story id `primitives-avatar--stack`) and
  [tree with an insertion row](../../packages/storybook/src/stories/TreeView.stories.tsx) (story id `views-treeview--insertion`).
- **One pager: `Pager`** (`ui/pager.tsx`) in `range` mode (lists) or `record` mode
  ("n / N" over a navigator's order; `RecordPager` is that mode behind a `nav`), with
  `formatNumber` as its default. A pane list's pager footer is `ResourcePaneFooter`.
  Owner story: [pager](../../packages/storybook/src/stories/Pager.stories.tsx) (story id `primitives-pager--default`).
- **A disabled reason is a tooltip.** A band verb that cannot run carries its
  `disabledReason` as a `Tooltip` and stays focusable while disabled; a menu item
  keeps the reason as its description row inside the menu (links stay usable). The
  band never renders reason sentences. Owner stories:
  [record action bar](../../packages/storybook/src/stories/RecordActionBar.stories.tsx) (story id `views-recordactionbar--default`) and
  [tooltip](../../packages/storybook/src/stories/Tooltip.stories.tsx) (story id `primitives-tooltip--default`).
- **The pane row's verb is a control beside the row**, not text inside its button
  or link: `PaneRow.verb` as a node renders the pane's own button or link next to the
  row's control, revealed on hover or focus; a string is a hint of what the row opens.
  The aside's scope overflow is the pane menu's radio group (`PaneToolbarMenu
  groups`), with the active overflow kind shown as a clearable scope chip, never a
  native `<select>` or an "N more" button. Owner stories:
  [pane rows and labels](../../packages/storybook/src/stories/PaneRow.stories.tsx) (story id `communication-panerow--default`) and
  [timeline rail](../../packages/storybook/src/stories/TimelineRail.stories.tsx) (story id `communication-timelinerail--default`).
- **One selected surface: the `selected` utility** (`styles/index.css`), composed
  through variants — `data-[pressed]:selected`, `aria-[current=true]:selected`,
  `data-[active]:selected` — by trees, toggles, tabs, pagers, section nav, pane rows
  and pressed chips. Never spell `bg-brand-soft text-brand-soft-text` by hand.
- **One in-flow list message: `ListStatusMessage`** (`fragments/ListStatusMessage.tsx`)
  for a group's error or empty note inside a table, board or pane list; `ErrorBanner`,
  `EmptyState` and `InlineEmpty` own every other error and empty surface. Owner story:
  [list status message](../../packages/storybook/src/stories/ListStatusMessage.stories.tsx) (story id `fragments-liststatusmessage--default`).
- **Status → tone is owned once** by
  [`statusTone`](../../packages/ui/src/widgets/status-tones.ts). Framework defaults
  contain only neutral vocabulary. Addons contribute product values through
  `defineAddon({ statusTones })`; composition normalizes keys and rejects duplicate
  claims, including claims on framework defaults. Every React surface resolves
  tones with [`useStatusTone()`](../../packages/ui/src/widgets/use-status-tone.ts),
  which reads the current app's runtime; custom surfaces use the same hook as
  `statusBadge` and form headers.
  The pure `statusTone` resolver remains for non-React transforms with explicit
  vocabulary. An explicit `<Column tone>` map wins, then addon tones, then the shared
  convention, else `brand`. Scoped `resources.<model>.fields.<field>.tones`
  colors one column without claiming a global status word; its option label
  remains the displayed text. Bare enum columns use `statusBadge`, and scalar
  stages can declare it. A field's backend `status_display` metadata selects the
  `pill` (default), `dot`, or `text` `StatusBadge` display everywhere that field renders;
  `<Column statusDisplay>` and `<Field statusDisplay>` are explicit authored
  overrides; there is no separate dot widget. A run
  state — stopped/running/error/warning — therefore uses the dot display (grey/green/red/amber);
  a value the vocabulary doesn't know takes an explicit `<Column tone>` (e.g. a task's
  `blocked`→`danger`). Keep the run state a separate field from a lifecycle/state enum
  rather than overloading one column with both axes.
- Route every user-facing string through i18n: `useUiT()` in `@angee/ui`,
  `use<Addon>T()` in an addon (created with `createNamespaceT(ns, fallback)`),
  with namespace-relative English keys in the namespace bundle. A prop whose default is a
  label defaults to `undefined` and resolves `?? t("key")` in the body — never call
  `t()` in a default parameter. No hardcoded copy in a component. Four boundaries
  stay plain English: an addon's declarative manifest menu/route `label:` and
  chatter/drawer contribution labels (chrome data, not in-component copy — none
  are routed), a form registered via `forms:` (a statically parsed element,
  never rendered as a component, so a hook cannot reach its `<Field label>`),
  and a dashboard widget row-column `label` (authored resource data).
- Every icon is a registered glyph rendered via `<Glyph name="…">` (or the
  `renderGlyph(icon)` slot adapter). A component never imports `lucide-react`
  directly: base glyphs live in `chrome/icon-registry.ts`; an addon contributes its
  own lucide components through the manifest `icons:` field (the registry seam), not
  by rendering them. Glyph ids are lowercase/kebab-case; the lookup normalizes
  requested names to lowercase, so camelCase registry keys do not resolve.
- Use shared page, resource, form, table, widget, and layout primitives before
  adding new local state. Never hand-roll a resource list (grid/list/group/board),
  form, or detail in an addon — compose the shared resource actions
  (`ResourceList`/`ResourceCreate`/`ResourceEdit`/`ResourceShow`), `List`/`Form`
  declarations, and record fragments (`RecordHeader`/`MetaGrid`/`MetricStrip`);
  for a linked cell, compose `NavLink`/`Chip`/`MetricTile`, never a bespoke link
  class, and for a list of values, `ChipList`. If a shared view lacks what your case needs, extend it in `@angee/ui`
  (the owner) so every addon gets it. The shared-primitive rule lives in the
  [constitution](../../AGENTS.md#constitution).
- **Routes and pages stay thin.** A route declares URL, layout, menu/chrome,
  refine resource, action, and component. A resource-backed page composes the
  standard resource action components with `List` and `Form` declarations; a
  daemon/remote/in-memory collection composes `RowsListView` or a named shared
  owner; a grouped or board-capable resource composes `ListView` with grouping
  and the matching backend aggregate/filter contract. Local action controls or
  hooks supply only domain-specific composition glue after checking existing
  owners. Small size does not excuse duplication. Pages do not own table
  mechanics, duplicate route params, cache state, bespoke loading/error surfaces,
  or local copies of shared resource-view state.
- A row verb is a `rowActions` declaration on `ListView`/`RowsListView`, never a
  hand-rolled trailing column with local `useConfirm`/`toast.danger` ceremony.
  Declare `presentation="icon"`, `"label"`, or `"both"`; labelled text is the default
  because a verb must remain understandable without recognizing its icon.
  Mark the state's next descriptor `primary`; keep infrequent descriptors at
  `placement: "menu"`. Secondary inline verbs appear on hover and focus.
- Editable lines are the data view in edit mode; compose `RowsListView` and its column, visibility, action, reorder and footer seams, never form-local tables.
- A list route declares its shipped `presetIds`, `search` shortcuts, `createAction`,
  and `boardCard` fields on `ResourceList`/`List`. Undeclared search shows the full
  combined box; declared or contributed shortcuts default it to a collapsed
  trigger. The box stays reachable below 36rem toolbar width, where shortcuts
  hide. Shortcuts compose the same model as the box; toggle ids may name filter
  options, presets or favorites. The route default preset joins automatically.
  Named filters that fail resource-query parsing and shortcuts with unavailable
  metadata-dependent targets throw with their ids in development; production
  omits them and logs each id and reason once per mount. Malformed declarations
  still fail fast in every environment.
  A scoped create verb uses a server-projected parent record for its permission;
  the create label comes
  from resource vocabulary. Each declared `chrome` key (`viewSwitcher`, `pager`,
  `columnChooser`, `search`) overrides the presentation's default without
  changing query state; `chrome.heading` declares label, hint, and audience
  around the live count. A nonselectable list hides Share but keeps other
  contributed utilities.
- **An embedded list is compact by default.** `presentation="embedded"` on
  `ListView`/`List`/`ResourceList`/`DrawerResourceList` replaces the control band
  with one heading row: the resource vocabulary's label (or `chrome.heading`) ·
  the live count, then `toolbarActions` and Create. The view switcher, column
  chooser and row selection stay hidden; search and pager return inline once the
  collection outgrows its page, leaves page one or carries a query beyond its
  default. `chrome.heading: false` hands the heading to a host that already
  shows the label and count, such as a dashboard widget; any other declared
  `chrome` key or `selectable` overrides the compact default. See
  [`listChromeState`](../../packages/ui/src/views/resource/resource-view-types.ts).
- **One collection is one list.** Never split a collection across two lists —
  not by foreign-key direction, by a subset such as the viewer's own rows, or by
  a category. Declare one list and express the split as a column, a search
  shortcut or a row lock (Person relationships carry a direction column;
  proposal reviews a "Mine" shortcut). Parts saved with their parent are
  `lines=` rows in `EditableLines`, not a list. Distinct collections may stack:
  related collections inside a record as compact embedded lists, page-level
  configuration collections as `SettingsSection`s that each keep their own
  controls row (storage settings: drives + backends).
- **The data view's client/server boundary is a row-model choice, not a fork.**
  Where list operations (filter/sort/paginate/group) resolve follows the
  established data-grid pattern — AG Grid's named *row models*, TanStack's
  built-in client row models vs `manual*` flags (Angee's grid *is* TanStack
  Table), MUI's `*Mode`. Choose the boundary by **dataset size, not data
  origin**: default to **client-side for small, bounded, computed collections**
  (one fetch, then filter/sort/paginate/group in the browser over the loaded
  set), and **server-side for large model-backed resources** (Hasura
  `where`/`order_by`/`limit` + the `_groups` aggregate). Grouping is a
  client-side row model by default (it needs the whole set); the server
  `_groups` surface is the escalation only when the data is too large to hold in
  memory. A computed/non-model source is exposed **once** as a Hasura resource
  (`hasura_pydantic_resource`) for the uniform fetch + metadata + MCP surface.
  A small one processes client-side over the fetched set; a large one keeps the
  server row model and declares `groupable` columns for the same `_groups`
  surface (Platform Fields is the example). Do not
  hand-roll a new client filter/sort/paginate engine — compose TanStack Table's
  row models through `useClientResourceViewSurface` over the fetched set for a
  `rowModel:"client"` resource; `RowsListView` remains the
  renderer for the genuinely non-resource in-memory case — the operator-daemon
  quarantine and bounded computed projections.
  Storage's `FileBrowserContent` is the contrasting server-backed example: it
  composes `List` over `storage.File` with drive/folder base filters and
  server-side folder grouping because a drive can contain hundreds of thousands
  of rows.
- **Large scoped projections use the native server collection seam.** Compose
  `ListView.source` with `collectionQuery` and an explicit `ResourceQuery`
  contract when the server projects rows from a changing record scope. The
  authored document owns variables, results and count units; the shared list
  owns filtering, grouping, virtualized rendering and independent group pages.
  `CollectionTreeView` composes the same transport with native tree expansion
  and child paging. Do not register a fictional model, infer available choices
  from one server page, or filter/group that page in the browser. Bounded
  in-memory fixtures still use `RowsListView`.
- Named addon `resourceViews` compose the existing favorites and ResourceView
  query state. A route or menu `defaultResourceView` selects a shipped preset;
  `presetIds` adds only the other presets declared for that collection route.
  Menu presets are admitted only on their target route. URL edits override the
  route default's editable query; toolbar Clear restores that default and its
  fixed filter. The switcher includes shipped views even without
  writable preferences. Columns use native TanStack visibility keyed by the
  authored field; relation display-path projection preserves that identity.
  See the [manifest contract](../../packages/app/README.md#app-vocabulary-and-shipped-views).
- **Card presentation does not change the query boundary.** An ordinary grouped
  board over a server resource uses the same server groups, exact counts and
  per-group record pages as the grouped list. Deriving its lanes from a flat
  record page hides groups that have no records on that page. The shared grouped
  surface owns discovery and paging; board components render its results.
  Explicit `laneSource` boards retain their relation-catalogue contract for empty
  lanes, drag ordering and lane creation; bounded local collections keep native
  client grouping. The read-only `gantt` view kind composes both owners: a
  `ListView` declaring `gantt` and `laneSource` pages its rows, empty ones
  included, through the board's relation-options owner and loads their bars
  through the list's resource query and batch transport. See
  [`GanttCollectionSurface`](../../packages/ui/src/views/gantt/gantt-collection-surface.tsx).
  `GanttViewSpec.current` names the lane field holding its current bar identity;
  the collection selects it and emphasizes that bar independently of selection.
  `GanttViewSpec.lane` selects its own fields and returns a linked title, optional
  secondary line, named people, and a short note after the last scheduled bar.
  Empty parts are omitted; the lane heading uses the lane resource vocabulary.
  Human dates, ranges and times come from the shared
  [`date-format`](../../packages/ui/src/widgets/date-format.ts) owner. The app
  runtime i18n provider sets the default language for pure formatters; explicit
  locales override it. Gantt passes its configured time zone to date ranges
  and week headers. Lane links use router navigation on plain clicks, with
  `onRowClick` taking precedence when the host supplies it.
  Optional `markers` declares a second resource's date and lane relation. Both
  sources load every record on the current lane page through their own query
  contracts; marker filters are independent of bar filters. Due dates render as
  the vendored grid's zero-duration diamonds, with no inferred task duration or
  write gestures. See the [spec](../../packages/ui/src/views/resource/resource-view-types.ts).
- **Resolve resource queries before adapting them to a library.** Use the
  resource's `ResourceQuery` for allowed comparisons, group identities, drill
  predicates and required selections. Hand-building a resource view's Hasura
  `where` instead of using `ResourceQuery.toWhere` is a bug: the query owner
  validates canonical intent and translates it to the transport. This rule does
  not prohibit variables for separate, authored GraphQL operations. A display
  label never identifies a relation bucket. Invalid URL or favorite query state
  must block dependent reads and offer recovery; dropping invalid constraints
  silently broadens the user's query. Regression tests must exercise native
  providers and table accessors, including grouping fields absent from visible
  columns. Public API cutover guidance lives in the
  [`@angee/ui` migration note](../../packages/ui/README.md#resource-query-migration).
- A recipe's icon-button size keys are `iconSm`/`iconMd`/`iconLg` (one spelling
  across recipes). A default `size` is a visual contract — do not flip it without a
  requester (differing defaults like `Switch`/`ToggleGroup` `sm` vs `Toggle` `md`
  are intentional, not drift).
- Primitive export convention: a primitive exports flat per-part consts; a compound
  primitive exposes a bare-name parts-namespace object (`Dialog.Root`, …); a
  primitive that ships a composed convenience component takes the bare name for it
  (`Select`, `Tooltip`) and exposes its parts under a `*Primitive` suffix only where
  a consumer compounds them (`SelectPrimitive`). Don't add a `*Primitive` namespace
  nobody compounds.
- State surfaces are shared fragments — never hand-roll an empty/loading/error
  block. The titled surfaces (`EmptyState`, `ErrorBanner`) take the one
  `{title, description, icon?, actions?}` vocabulary; the single-line ones keep
  their own slot (`InlineEmpty` `label`, `LoadingPanel` `message`). For a
  full-height empty panel pass `EmptyState fill` (it centers an intrinsic-size
  card) instead of wrapping it in a `grid place-content-center` div. Use the
  unboxed `LoadingPanel` only when the final geometry is unknowable; the router's
  code-split boundary knows only that a console page comes next and takes its
  neutral `shape="page"`. A renderer that knows its final geometry owns a
  shape-preserving skeleton built from `Skeleton` and one `SkeletonStatus`; retain
  settled content during background refresh. The renderer owns loading/error so
  callers describe only the happy path (cf. `preview/builtins.tsx` `FileText`).
- Forms are declarative even when they branch: a `<Field showWhen={(values) => …}>`
  predicate (mirroring `Action.visibleWhen`) drives a discriminated form — a `kind`
  select that swaps the body — and a hidden field is never submitted. Reach for a
  custom form component only when the declarative DSL genuinely cannot express it.
- **A record's own fields are its sheet; tabs live only beneath it.** `FormView`
  renders the header, body field and every group as the sheet; labelled groups are
  titled sections, so group fields for reading order. A group of secondary facts the
  reader opens on demand (a message's envelope) may instead be a pane:
  `<Group label=… pane="id">` renders its fields beneath the sheet as that pane,
  bound to the record's form. Panes come in strip order: the editable lines when no
  `<Group lines>` places them in the sheet, then the saved-record tabs left after
  `visibleWhen`, permissions and the record's implementation, then pane groups; all
  share one id namespace. Two or more panes get a strip; one renders without one,
  beneath the sheet under a `SectionHeading` with its label. A full-bleed pane
  fills the height beneath the sheet, which then scrolls in its own region. `defaultRecordTab` (or its rule), a controlled
  `recordTab` and `?recordTab=` choose among the panes; an id that names no visible
  pane falls back to the first pane without rewriting the URL. Field reveal focuses
  a sheet field in place and selects the pane holding a pane-group or line field.
- **Never nest tabs.** A pane is the last level of tabs: its content carries no
  strip of its own and no second heading row repeating what the pane names. An
  embedded list that fills a pane sets `chrome: { heading: false }`; content that
  needs its own sections is a sheet group or another pane, not tabs inside a tab.
- **The form hero precedes secondary facts.** `FormView` places its status control
  above the title, except statusbar fields: these occupy the title row's right, wrap below on narrow widths, omit their label and body copy, and remain header badges in compact forms. Its lead body precedes the sheet's groups. A domain-owned
  status control declares `<Field status widget="…" />` and registers its widget
  with the addon. Add `fill` when it should use the measured hero width; the
  widget receives `field.fill` and `field.containerWidth`. Do not repeat that
  state as another strip.
  The resource's `contextFields` metadata owns the facts line beneath the title:
  name fields whose native read widgets should render there (relation labels,
  money with its declared currency field, dates), separated by middle dots.
  `FormView` and `ResourceList` accept the same prop as an explicit override.
  The framework appends readable `created_by` and `updated_by` attribution,
  preferring each audit field's existing `*_label` projection;
  public ids and raw created/updated timestamps appear only in developer mode.
  `contextLine` remains the deliberate custom-rendering escape hatch. Place
  operational fields in a collapsed Details group when their create-default
  behavior must remain available.
  Projects exports its [standard declarations](../../addons/angee/projects/README.md)
  so consumer routes compose the same forms, lists and record tabs.
  See the shipped [FormView hero](../../packages/storybook/src/stories/OwnerSpecimens.stories.tsx) (story id `library-owners--forms`).
- **Statusbar steps are projections of owner facts.** `StatusbarSteps` renders only
  steps declared on the path, and a side or terminal state as one muted chip.
  Server-owned transitions declare `selectable` from eligible choices, and the
  owner confirms before writing. Plain form options update form state until Save.
  Use `fill` for record-width bars and pass the slot's available `containerWidth`
  when it is known. Enum labels for status controls and cells resolve through
  `canonicalOptionValue`, regardless of GraphQL read casing.
- **Contribute a saved-record tab from the data view** as a `<model>#sections`
  child holding a direct `<Tab>` declaration. Canonical parent sections are
  inherited by concrete child forms; contribute once at the owning model. Declare `requiredFields` for the tab's `visibleWhen` predicate,
  which evaluates the loaded record and the form's data resource; fields omitted
  by a child projection are read from the canonical resource. A tab for every
  record kind is a `form#sections` child gated on the resource fact it reads.
  Changes are Timeline lines and the changeset drill-in; attached work is the
  Links tab; neither is a saved-record section.
  Inside a section panel, use
  `useRecordChromeContext()` and scope a related collection with a base filter
  on a `presentation="embedded"` list, which brings compact chrome and local
  state. The model's Hasura resource owns filter/order/group/facet capabilities;
  the list owns its heading row, paging and `rowActions`, including
  confirmations for generated action callbacks. See
  [Integration Streams](../../addons/angee/integrate/web/src/IntegrationStreams.tsx).
  A large browsable set inside a record (child runs, trigger events, a project's
  tasks) keeps the full list: the default presentation with `scope="local"`.
  A product narrows a form's sections and verbs per app or route with `only`
  under `when` on `form#sections`, `form#actions` and `form#actions-menu`; a
  container nobody narrows keeps all its children and `only: []` keeps none.
  [FormView](../../packages/ui/src/views/form/form-view-surface.ts) selects
  required fields from the resolved children; authored fields and passive chrome
  remain host-owned.
- **Record verbs compose the shared action owner.** The primary toolbar is
  reserved for the subject model's lifecycle transitions, the ones its statusbar
  explains (post, cancel); secondary lifecycle verbs and model-owned utilities
  (pay, reverse, send, PDF, merge, download) live in the overflow menu; a
  capability contributed to another model (run a workflow, rate, generate a
  delivery, read a file again) uses that model's overflow menu, never its
  toolbar. Classify an action by the subject it changes and its semantic owner:
  a bridge addon implementing an inherited lifecycle (connect a channel) is not
  a foreign capability because it lives in another package. Process drill-ins
  expose the verbs of the inspected run, decision or changeset (answer, stop,
  retry, revert); collection controls stay with their collection. Every
  placement composes the shared action owner, projected permission/admission,
  the dirty/pending gate and the applicable confirmation. `form#chrome` carries
  the record-header controls that are about the record rather than verbs on it:
  marks and status, Share and the effective-access explanation, Follow, Needs
  you. Those controls mutate only their own metadata (grants, visibility,
  subscriptions); they never execute a business lifecycle or change content.
  Grants, effective readers and followers are three facts: a direct tuple is
  not effective access, a reader is not a subscriber, and following or being
  made a recipient never grants access. There is no Access aside tab and
  grants are not a Related group; a model's own audience and role adapters
  (visibility, responders, rosters) compose the same Share owner. A verb
  in the toolbar is a
  `#actions` child and one in the overflow menu a `#actions-menu` child; either
  may render [RecordActionBar](../../packages/ui/src/views/form/RecordActionBar.tsx)
  with server-gated descriptors. [Record chrome](../../packages/ui/src/views/form/use-form-view-record-chrome.ts)
  carries the form's dirty/pending gate to toolbar and menu verbs. `<Action>`
  and record-verb children declare a projected `permission` when their verbs
  require one; unavailable verbs are omitted. A verb's `visibleWhen` reads its
  owner's projected admission (`task_actions`, `account_actions`, `can_open`),
  never a client copy of the server rule. RecordActionBar confirms every
  `danger` verb that runs on click: its `confirm` copy in the danger tone, or the
  standard copy titled by the verb. Declare `confirm` only for domain copy.
  See the shipped [record action bar](../../packages/storybook/src/stories/RecordActionBar.stories.tsx) (story id `views-recordactionbar--default`) and
  [command band](../../packages/storybook/src/stories/CommandBand.stories.tsx) (story id `page-commandband--default`).
  A read-only inspector with only secondary actions composes `ActionMenu` and
  `RecordChrome` in `RecordHeader.actions`; it does not reserve an empty command
  band. Collection and transcript controls remain in their owning pane toolbar.
- **Trash composes the shared owner.** A resource whose metadata marks a field
  `trashable` gets Trash and Restore from [the trash module](../../packages/ui/src/views/resource/trash.tsx):
  `useTrashActions` for record forms, `useTrashRowActions` for lists, and
  `RemovedRecords` (or `RemovedDisclosure` + `RemovedItem` over rows a surface
  already holds) for its "Removed (n)" list. Each offers its control by the
  row's projected `delete`. A domain verb with its own transport, such as record
  chatter moderation, still asks through `useTrashPrompt`.
- **Record rails reuse form fields.** Contribute a `FormView.RailGroup` as a
  `<model>#rail` child with standard field descriptors and optional
  group/row permissions. The form selects those fields and binds them to its
  save state; unreadable rows stay absent. The rail sits beside the sheet and
  stacks beneath it in a narrow container.
- **Inline visibility controls bind a server verb.** Declare a
  `<Field name="visibility" widget="visibility" placement="title" visibilityAction={...} />`.
  The [shared widget](../../packages/ui/src/widgets/visibility.tsx) uses record
  chrome's server choices, revision and action gate; [Task fields](../../addons/angee/projects/web/src/task-actions.tsx)
  and [Answer fields](../../addons/angee/proposals/web/src/index.tsx) declare the binding.
- **Records have one chronology.** The timeline addon owns the `Timeline`
  tab and the `timeline` read, which pages a SQL `UNION ALL` of native source
  queries behind one cursor. Each addon contributes a Python
  [timeline kind](../../addons/angee/timeline/kinds.py) and a `timeline#kinds` presentation (label,
  glyph, verb, target). A line is a pointer: one sentence, actor, time, one
  verb, never a body, never an action. A run folds its decisions and changes
  into its line; the run drill-in shows one run with its own Stop. The same
  `DecisionCard` serves the inbox and the run drill-in. Forms consume the
  shared field-mark bridge; a mark's "View change" opens the changeset
  drill-in with the field highlighted. Needs-you and Follow are record-header
  controls computed by their owners across the record, independent of what the
  Timeline has loaded. Lists may opt into `attentionColumn`, which combines
  open decisions and tags marked for attention in one badge cluster. An
  attention tag's normalized name is its `statusTones` key, declared once by
  the addon that owns that vocabulary; there is no attention-column tone map.
  Owner changes invalidate related records through their declared change feeds
  after commit; the timeline fragment subscribes to its exact record,
  with no list of foreign source models. Batched authored reads carry the same
  exact-record interests as singleton reads, without polling.
- **Unreadable projected fields remain legible as restrictions.** A record row's
  existing `redacted_fields` ledger lets `RecordFieldMarkControl` show the field
  label and a lock without its value. Compose that projection; never issue a
  field-by-field permission query or render a misleading empty value.
- A relation field is a link, not a dead end. A routed collection page tags its
  refine resource on the route — `{ name, path, component, resource:
  "integrate.OAuthClient" }` (one route per resource, build-time fail-fast) — and the
  relation widget resolves its registered record route. Read-only fields render
  `RecordReference` links; editable pickers show a "follow" arrow. Refine owns the route
  trail, while the routed record surface replaces the generic action leaf with
  the model's `recordRepresentation`. A resource with no routed page simply shows
  the retained label without a link.
- Register a resource's create form once via `defineAddon`'s
  `forms: { "integrate.OAuthClient": <…Field/Group children…> }`; the standard renderer uses it
  wherever that resource is created, including the relation-picker inline create. Use
  it when the create input diverges from the read projection (write-only secrets,
  scalar-id pickers, a kind discriminator). With a registered form,
  `RelationPicker`'s `create` needs only `{ resource }` (the override supersedes any
  passed `fields` on create); pass inline `fields` only for a data-dependent form
  whose options are fetched at runtime and so cannot be a static registration.
  `RelationPicker` also offers inline **edit** (a pencil beside the picker opens the
  *selected* record in a form dialog) — wired by `RelationFieldWidget` from the
  related model's fields, so a relation is created, edited, and followed without
  leaving the parent form. The create-form override stays create-only: an edit
  dialog renders the passed `fields` (the registered form is not reused for edit).
- **An MTI parent's inline create chooses a kind.** A picker on a model whose
  metadata lists `concreteKinds` creates through one dialog with a kind switcher,
  and selects the new row under the parent. The switcher decides only what is
  created now; consumers declare nothing. See
  [`useRelationForms`](../../packages/ui/src/views/relation/RelationRecordDialog.tsx).
- Toolbar and record action menus compose [ActionMenu](../../packages/ui/src/toolbars/ActionMenu.tsx).
  Contributions use `ActionTrigger` to adapt between a toolbar button and a native
  menu item and report pending state to the menu trigger. Render menu-opened
  dialogs inside the menu: the shared owner keeps them mounted after it closes
  and returns focus to its toolbar trigger. `DialogContent` resets menu context
  for its body, so nested dialogs return to their own triggers. Pages supply
  domain labels through i18n and alignment through `align`; selection menus keep
  their own native controls.
- Toolbar dialogs compose [MutationDialog](../../packages/ui/src/views/form/MutationDialog.tsx).
  Declare `DescriptorField`s; the shared [DescriptorFieldList](../../packages/ui/src/views/form/DescriptorFieldList.tsx)
  owns controls and requires a native RHF `FormProvider`. Decode raw controls with
  `parseValues` and `mutationDialogValueCodecs`; required/verbatim codecs express
  authored field contracts. Record actions declare `args` + `submit` on `<Action>`;
  `RecordActionBar` composes `ActionFormDialog` for their inputs. Record-specific
  schemas compose `jsonSchemaActionArgs` in the action's `args` callback; the
  shared form owns branching, validation and draft retention across record refreshes.
- Submit owners return [FormSubmitResult](../../packages/ui/src/views/form/validation-errors.ts):
  `ok` acknowledges saved data; `invalid` carries `ValidationErrors`; `conflict`
  preserves edits and offers reload. Typed GraphQL successes use
  `savedFormSubmitResult`; thrown Django validation and revision refusals use
  `formSubmitError` (VALIDATION and STALE_REVISION). Do not add another in-band
  form transport. Adapt ActionResult wire responses with
  `actionFormSubmitResult(data, root)` and normalized outcomes with
  `actionOutcomeSubmitResult(outcome)`. [applyFormErrors](../../packages/ui/src/views/form/validation-errors.ts)
  owns exhaustive narrowing and field/summary binding; malformed contracts throw.
  The submitting owner shows `ok.message` once. [FormView.submit](../../packages/ui/src/views/form/use-form-view-save.ts)
  returns `FormSubmitResult<Row | FormSubmitAcknowledgement>`; missing mutation
  data is an invalid result, never an `ok` null sentinel.
- Schema-driven forms import [createJsonSchemaResolver](../../packages/ui/src/views/form/json-schema.ts)
  from `@angee/ui/views/json-schema`. Ajv owns schema validation, formats and
  discriminator selection; RHF owns original and transformed values. Keep this
  opt-in adapter out of the UI main entry so other forms do not load Ajv.
- A widget that consumes a fixed array of object fields declares
  `acceptsRowTemplate: true` in its widget definition. The FormSpec projector
  passes the parsed `rowTemplate` only through that seam and rejects a selected
  widget that cannot accept it; compose the shared `rows` widget for fixed-size
  tables. The exported `DecisionCard` renders concerned records, proposal
  alternatives, evidence, and the chosen verdict for any question.
- Graph editing composes [GraphEditor](../../packages/ui/src/views/GraphEditor.tsx);
  consumers own connection policy, selection and persisted layout.
- Filter entry composes [FilterClauseEditor](../../packages/ui/src/toolbars/FilterClauseEditor.tsx);
  custom pickers retain their own keyboard interaction.
- Form undo composes [useFormHistory](../../packages/ui/src/views/form/use-form-history.ts);
  group field interactions and reset history when accepting a saved or reloaded baseline.
- Editable named entries use the [keyed collection](../../packages/ui/src/views/form/keyed-collection.ts)
  in authored order; client identities survive renaming and own duplicate-key issues.
- Schema path selection composes [SchemaPathPicker](../../packages/ui/src/views/SchemaPathPicker.tsx)
  with lazy branches, concrete indices and literal keys; schema owners resolve references.
- A labeled control is a page element or a `FieldRoot`. Reach for `FieldRoot` /
  `FieldLabel` (the stacked label-over-control owner, e.g. for an ephemeral
  composer not bound to a model record) before hand-rolling a `<label>` wrapper;
  `FieldRoot.Control` is the bound input when the form is not a FormView.
  A native input pairs `FieldLabel htmlFor` with the control `id`; a button-trigger
  control (a `Select`) labels via `FieldLabel nativeLabel={false} render={<span/>}`
  + the control's `aria-labelledby`.
- **Share is shared record chrome.** IAM contributes the generic
  [ManageAccessDialog](../../packages/ui/src/views/access/ManageAccessDialog.tsx)
  as a `form#chrome` child and a `resource#utilities` child. Models declare `rebac_grantable`; pages inherit
  Share from their resource metadata. Saved custom record surfaces compose
  `RecordChrome`, as forms do. List actions use the enclosing saved record when
  present, otherwise the collection owner's selected ids. Subject pickers read
  the resource's declared `subjectField`; never construct a subject from a
  public display id. The shared package owns presentation, and IAM owns the
  generated GraphQL document adapter.
- Live model updates ride GraphQL subscriptions through
  refine's live provider and react-query invalidation, not a `setInterval`
  refetch loop. Opt a model into live cross-actor refresh by declaring
  `changes(Model, field="<model>Changed")` in its `schema.py`; local writes
  invalidate through refine mutations, and subscription pushes invalidate the
  affected refine resources. Authored reads receive pushes through the
  [live coalescer](../../packages/refine/src/query-invalidation.ts): a change
  cancels matching in-flight requests at once and one refetch per burst follows
  within the max wait, so declare each read's `models` from the owners its
  resolver actually reads. Refine's own resource hooks still invalidate per
  push. Stream foreign-system state (e.g. the operator
  daemon's `onWorkspaceStatusChange`) over its own subscriptions.
  A dashboard widget may explicitly declare interval refresh through its
  [refresh contract](../../packages/ui/src/dashboard/headless.ts). Its data
  adapter forwards that interval to native Query `refetchInterval`; Query owns
  scheduling and observer cleanup. This declaration does not authorize a
  private polling loop in a page or addon. Use `setInterval` only for non-data
  UI motion or rotating a short-lived credential. Foreign systems should
  publish their own change subscriptions.
- Authored mutation result envelopes are decoded at the hook boundary. Pass
  `errorFrom` to `useAuthoredMutation` for `{error, error_code}` payloads; the
  hook throws before invalidating, so pages do not repeat result-error checks or
  accidentally refresh failed writes. Its native pending lifecycle includes
  result validation and awaited invalidation. Clear settled failures through
  the hook's `reset`; resetting a pending mutation detaches its observer without
  cancelling the write, so a dismissing dialog must preserve that pending state.
- Client-side gates are UX only. The server is the authorization boundary.
- **Presence reflects permissions; it never gates.** A menu entry (app roots
  and legacy list items too; dependents and the deployment may set it like any
  declaration field) or a container child may name `requires:
  "<app_label.ModelName>#<permission>"`. The identity read asks
  `current_user.permitted` about every composed ref; an entry the session
  lacks leaves the rail, menus and palette with its subtree, a child leaves its
  container, and routes stay reachable. Each ref is evaluated at type level,
  where row-specific arms are false, so name a role- or const-decidable
  permission (`iam.User#read__last_login`). A question about one row stays the
  child's per-row `permission`; data stays with querysets and field values with
  their gates. See [IAM presence](../../addons/angee/iam/README.md#presence).
- No Python view DSL, no frontend metadata hidden in backend decorators.

## Containers

A container is a named, ordered list on a node that the node's owner renders:
a form's sections, a record's chatter tabs, the user menu. An entry in it is a
child. Addons extend each other's pages by adding, moving, removing, hiding or
narrowing children, layered along addon dependencies as menus are. The
[compiler](../../packages/app/src/containers.ts) validates and layers the
addons' declarations at composition; the contracts and `useContainer` live in
[`@angee/ui/runtime`](../../packages/ui/src/runtime/containers.ts).

### Addresses, kinds and inheritance

A container's address is `node#name`; one child is `node#name/id`. The
framework's record containers sit at a kind-level node and also take a model
node: a child of `form#sections` shows on every model's form, a child of
`projects.Task#sections` only on Task's. A record renders the kind address,
then its canonical (MTI parent) model's address, then its concrete model's,
merged into one order, so a child of `parties.Party#aside` also shows on
Party's MTI children. Model spellings are canonicalized at composition.

The name after `#` types the entry. Each owner adds its name and content type to
the `ContainerKinds` interface, so a `#aside` child carries
`ChatterTabContent`, a `#views` child `ResourceViewKindContent`, and a
`#sections` child the `Group`, `Action` and `Tab` declarations a form parses.
Contribute those markers directly: the form parser does not render a component
to discover declarations inside it. Use `LabelKey` for translated marker labels
and put components in their rendered content slots.
There are no per-kind constructor functions; an addon helper returns a plain
child (`recordPagesTab()`, `decisionRecordTab()`). A new
container name is added by declaration merging, as
[IAM](../../addons/angee/iam/web/src/ShareAccess.tsx) does:

```ts
declare module "@angee/ui/runtime" {
  interface ContainerKinds {
    "access-roles": React.ComponentType<AccessRoleOwnerProps>;
  }
}
```

### Authoring

An addon declares one `containers` dict keyed by address. A key in the addon's
own namespace (`<id>.…`) declares a child; a second declaration of an id at one
address fails. From [work](../../addons/angee/work/web/src/index.tsx):

```tsx
containers: {
  [`${TASK_MODEL}#sections`]: {
    "work.task-fields": { sequence: 40, content: taskWorkFormSection },
  },
  [`${TASK_MODEL}#actions`]: {
    "work.task-triage-actions": { sequence: 40, content: <TriageRecordActions /> },
  },
},
```

A child carries `content` and, optionally, `sequence` and `before`/`after`
(its position), `permission` (a projected record permission the row must hold,
checked by owners rendering for one record), `requires` (presence: a ref the
session must hold, whatever the row; see Rules), `requiredFields` (readable
fields it consumes, which the form selects), `impl`, `variant` and `key`.

Any other key alters a child an addon this one depends on declared:
`sequence`, `before` and `after` move it (at the address it was declared at),
`remove: true` takes it out at composition, `hide: true` hides it at render and
`hide: false` shows again what a dependency hid. The framework's own children
are every addon's to adjust. Two unrelated layers setting one field fail.
[Messaging](../../addons/angee/messaging/web/src/index.tsx) contributes the
presentation of the timeline kinds its Python source implements:

```tsx
"timeline#kinds": {
  "messaging.comment": { content: { label: "Comments", icon: "comments", verb: "Discussion", target: { tab: "messaging.discussion", focus: "message" } } },
  "messaging.conversation": { content: { label: "Conversations", icon: "channel", verb: "Open conversation", target: { tab: "messaging.conversation" } } },
},
```

An addon declares a container of its own by naming an address on its own node,
with no children or with its own: `"messaging.channels#toolbar": {}`. Two
entry keys apply only there. `unique: "key"` makes every child carry a `key`
and fails two children with one key. `models: true`
makes the container model-scoped: IAM declares `iam#access-roles`, and
[proposals](../../addons/angee/proposals/web/src/record-rounds.tsx) adds
children at `proposals.Round#access-roles`; the owner passes the record's
models to `useContainer`. A model-scoped name belongs to one kind.

Unknown addresses, unknown children, unknown keys and ids outside the
addon's namespace fail composition at app boot; `pnpm run test` composes the
full addon set.

### Narrowing and `when`

`only: [ids]` keeps the listed children and `except: [ids]` drops them;
`only: []` keeps none. Narrowing applies at render and per layer: each layer's
`only` intersects with what it inherits and never filters children the
layer's own dependents add. Narrowing an addon's container requires depending
on that addon; the framework's containers are open to every addon.

`when: { app, route }` limits an entry's render verbs (`only`, `except` and
`hide`) to some pages. `route` matches the route or any route below it, `app`
any app on the page's menu trail (its root and every included app on the way,
flattened ones too, so `when: { app: "work" }` holds on work's pages inside the
PM suite; with an app selected, a page outside its rail sits in the selection's
home root alone). Each takes one id or a list, and an id that names no route or
app fails at boot. An array of entries holds conditional alternatives:

```ts
// A product layered on its dependencies.
containers: {
  "form#chrome": { only: ["iam.share-record"] },
  "shell#drawers-bottom": { only: [] },
  "record#aside": [
    { only: ["timeline.rail", "related"] },
    { only: [], when: { route: "projects.my-work" } },
  ],
},
```

Children are declared unconditionally; declaring, moving or removing under
`when` fails. A container has no hide of its own: hide its children, or narrow
it with `only: []`. `hide: false` undoes a `hide`, never an `only`. The
deployment's `ANGEE_UI.containers` is a last layer that depends on every addon:
it alters and narrows, and declares nothing. A deployment `only` narrows like
any other unless it says `force: true` (G-14): then, where it holds, it stands
in for every addon's `only` and `except` across the addresses the page merges
(a forced `only` at `notes.Note#sections` also sets aside the addons' narrowing
at `form#sections` on notes forms), so a deployment can admit a child an addon
narrowed away; a `hide` stays until the deployment shows it again. Only the
deployment may force, and only beside an `only`. Developer mode labels such a
rule "force only". `ANGEE_UI.menus` forces the same way, per menu node.

### Variants and `impl`

Vary a child per row by the implementation the row carries (its
`ImplClassField` value). A child with `impl: "<key>"` shows only on rows of
that implementation. A child with `variant: { of, impl }` stands in for the
child `of` on rows of that implementation and leaves the original on other
rows. A variant inherits its original's admission: an `only` that drops the
original drops its variants, and a `hide` of the original hides them. Where
the row lacks the variant's `permission`, the original stays. Two variants of
one child for one `impl` fail composition. The
[channel bridges](../../addons/angee/messaging/web/src/channel-bridge-addon.tsx)
are the reference: each vendor's verbs are `impl` children, and its pairing
verbs are variants of Integration's resume and disconnect:

```tsx
[`${CHANNEL_MODEL}#actions-menu`]: {
  [`${id}.disconnect`]: { variant: { of: INTEGRATION_DISCONNECT_ACTION_ID, impl: key }, sequence: 13, content: disconnectAction },
},
```

Owners pass the row's implementations where they render for one record. The
form does so for all of `#sections`, `#rail`, `#actions` and `#actions-menu`:
it resolves each with `projection: true` so the record projection reads every
candidate's fields, then shows the groups, tabs, actions and rail groups of
the children the loaded record admits; its layout (title, status, body) and its
save read only those children's fields. An original and its variants may reuse
a tab or rail group id, never a fixed group's. A create form knows no
implementation: originals stand, and the children that need one stay out of
the form with their required fields and defaults.

Record verbs are `#actions` children. The one exception is an inline action
form rendered inside the record, such as a decision answered on its own page:
its submit button must share the form's React Hook Form state, which a container
child cannot reach, so `ActionFormDialog` with `inline` portals that button into
the toolbar through `RecordChromeContext.toolbarHost`
([record chrome context](../../packages/ui/src/views/resource/record-chrome-context.tsx)).

### Framework containers

| Address | Children | Content |
|---|---|---|
| `form#sections` | `Group`, `Action` and `Tab` declarations on a record form | `ReactNode` |
| `form#rail` | `FormView.RailGroup` beside a saved record's tabs | `ReactNode` |
| `form#actions` | record verbs in a saved form's toolbar | `ReactNode` |
| `form#actions-menu` | record verbs in a saved form's overflow menu | `ReactNode` |
| `form#chrome` | record-header controls at the toolbar's right edge: marks, status, Share, Follow, Needs you | `ReactNode` |
| `resource#views` | view kinds a collection's switcher offers | `ResourceViewKindContent` |
| `resource#utilities` | collection utilities beside a resource view's toolbar | `ReactNode` |
| `resource#search` | separate controls over the list search model; page shortcuts are `page.*` extras | `SearchShortcut` |
| `record#aside` | context-aside tabs (`strip: true`) and drill-ins (`strip: false`, with a target) | `ChatterTabContent` |
| `timeline#kinds` | presentation of a timeline kind a Python source implements | `TimelineKindContent` |
| `related#groups`, `<model>#groups` | one group of the Links tab per edge: rows that link to their records and one verb | `RelatedGroupContent` |
| `shell#notices` | notices below the console navigation | `ReactNode` |
| `shell#user-menu` | user-menu items between the theme item and sign-out | `ReactNode` |
| `shell#drawers-right`, `shell#drawers-bottom` | non-modal drawers docked on that edge | `DockedDrawerContent` |
| `auth.login#method` | sign-in methods on the login page | `ReactNode` |
| `auth.login#password-help` | password help on the login page | `ReactNode` |

The `form`, `resource` and `record` containers also take model addresses. The
declarations are `FORM_CONTAINERS`, `RESOURCE_CONTAINERS`, `CHATTER_CONTAINERS`
and `SHELL_CONTAINERS` in `@angee/ui` and `LOGIN_CONTAINERS` in `@angee/app`.
Addons own theirs, such as `messaging.channels#toolbar`,
`parties.overview#items`, `appearance.settings#tools`, `decisions#origin`
and `iam#access-visibility`.

### Context aside

Owner stories: [Chatter and its panes](../../packages/storybook/src/stories/Chatter.stories.tsx) (story id `communication-chatter--default`),
[timeline rail](../../packages/storybook/src/stories/TimelineRail.stories.tsx) (story id `communication-timelinerail--default`),
[related pane](../../packages/storybook/src/stories/RelatedPane.stories.tsx) (story id `communication-relatedpane--default`),
[step list](../../packages/storybook/src/stories/StepList.stories.tsx) (story id `communication-steplist--default`),
[pane toolbar band](../../packages/storybook/src/stories/OwnerSpecimens.stories.tsx) (story id `library-owners--pane-toolbar`),
and [pane rows and labels](../../packages/storybook/src/stories/PaneRow.stories.tsx) (story id `communication-panerow--default`).

The context aside is the shell's right region: what is around the record. Its
children are `record#aside` and `<model>#aside` contributions. A `record#aside`
child shows on saved model-backed record views; the shared create-route sentinel
does not identify a record. A model-level child shows on that
model's pages and may narrow itself further with its content's `when`. A
`ChatterTabContent` carries `label`, `icon`, `render` and `strip`, and
optionally `when`, `count`/`useCount`, `target` and `panelClassName`. Every
pane renders into the shell's `PaneToolbar` band (scope · selector · search ·
actions · overflow; back and title first on a drill-in) instead of its own
header row; the same band heads explorer panes on the left.
Collections in these panes keep their native resource query/state owner and use
`ListView.renderPaneRow` for pointer rows and explicit `pane` for compact
selector and scope chrome. Row anatomy does not select a toolbar: omit `pane`
to keep the standard resource toolbar and its pager. With `pane`, the shared
toolbar publishes search, chips, ordering, grouping and utilities into the pane
band and the collection pager goes below the rows. `search.box: true` exposes
the same search control below the compact band. Group expansion and paging
remain owned by the resource collection in either presentation.

**Four tabs are in the strip, in this order**, each only where its capability
exists. A tab serves a recurring record-wide job (reading what happened,
talking, planning, finding what is linked), including its empty state and first
action; everything that answers a bounded question about one object is a
drill-in:

| id | seq | shows when | holds | owner |
|---|---|---|---|---|
| `timeline.rail` | 10 | the view is a model-backed record with at least one eligible kind | one chronology, newest first, one line per event, scope chips with counts | timeline |
| `messaging.discussion` | 20 | the model composes the chatter thread (`messaging.ThreadAttachment:chatter` in `recordEdges`) and the body is not itself the conversation | the record's thread: comments, internal notes, emails sent from here, the composer | messaging |
| `projects.tasks` | 30 | `projects.Task` in `recordEdges` and the body does not own the task plan | tasks about the record in Planned and Done scopes, schedule and assign | projects |
| `related` | 40 | at least one `related#groups` or `<model>#groups` child is eligible | groups Files (upload), Pages (bind), Sources (external links), Conversations (source threads); Attached to on files; Backlinks and Bound to on pages | framework tab; storage, knowledge, integrate, messaging own the groups |

A tab earns the strip by creating things; everything else is a **drill-in**: a
`record#aside` child with `strip: false` that renders one object named by its
`target` (a run, a changeset, a decision, a conversation). The rail opens it
with `setActiveTab(id, { from, target })`; the shell shows "‹ Timeline" above
it, keeps a return stack with filters, window, scroll and focus, writes
`chatterTab`, `chatterTarget`, `chatterFocus` and `chatterFrom` to the URL, and
shows an unavailable state with Back when the target cannot be read. Hiding a
destination from the strip never unregisters its drill-in. The record's own
state belongs in the header and sheet; a drill-in acts on the record's process
(answer, stop, revert) and never edits its content.

Messaging's [`messaging.conversation`](../../addons/angee/messaging/web/src/index.tsx)
drill-in targets a `ThreadAttachment`. Its pane resolves the thread through the
authorized `record_source_threads` read and composes `ThreadTranscript`.
Conversation rows in Links continue linking to the thread page.
Message matches open the full conversation at the selected message; inspecting
the message record is a separate secondary action. Messaging's
[`MessageReplyQuote`](../../addons/angee/messaging/web/src/MessageReplyQuote.tsx)
renders the access-checked parent in readers, transcripts and record discussions.
Readers and transcripts navigate quotes through the host when retaining an
explorer scope, or through the same-thread anchor or native record link.

A tab that lists one polymorphic edge (files, pages, sources, the chatter
thread, tasks) shows only where the record's type can carry it: Links groups
declare `edge.model`; other tabs check it in `when`. The framework checks the
edge's model label in `context.route.recordEdges`, the resource
metadata the backend derives from each edge's schema target relations. Timeline
sources use the same fact to decline unsupported records; workflow run evidence
accepts any canonical REBAC record. Never let a pane report the edge
"unavailable" or offer writes on a type whose schema declares no arm. A section
with neither rows nor a verb is absent, not empty.

Mount count hooks in independent keyed readers. Route and permission changes
can change the eligible groups while the aside stays mounted; aggregating hooks
in a loop changes their order. The [Related count owner](../../packages/ui/src/communication/RelatedPane.tsx)
sums the results published by each group reader.

Each `related#groups` child (or `<model>#groups` for one model's pages) is one
group of the framework's Links tab, declared by the addon that owns the edge.
Use **Backlinks** for incoming references, such as pages citing a record or
messages using a file. The tab includes both directions; the existing `related`
tab id and `related#groups` contribution addresses remain stable.
The tab draws the group's label, count and action; the group supplies its rows
through the shared pane row anatomy:

```ts
interface RelatedGroupContent {
  when?: (context: ChatterViewContext) => boolean;
  edge?: { model: string };
  label: ReactNode;
  useCount?: (context: ChatterViewContext) => number | undefined;
  useVerb?: (context: ChatterViewContext) => boolean;
  render: (context: ChatterViewContext, label: (action?: ReactNode) => ReactNode) => ReactNode;
}
```

A group with neither rows nor a verb is absent, not empty. `useVerb` is the
presence rule, including write restrictions such as view-as. `render(context,
label)` returns one component with local state. It calls `label(action)` for the
framework's counted `PaneLabel` and places its row content in a sibling `li`.
Only when `useVerb` is true does the label show the action. Compose
`PaneLabelPopover` for a small Upload or Bind picker; the component's upload or
mutation state survives the popover closing. Count and presence probes never
mount that component or its write state.

`edge.model` makes a group eligible only when that exact model is declared in
the route's `recordEdges`; `when` adds optional narrowing. Addons own role claims
through typed content extending `RelatedGroupContent` and use
`useRelatedGroups(context)` to find eligible siblings after model scope,
container narrowing and `when`. A generic group excludes those roles from both its
rows and count; a role-specific group lists its own role. Knowledge's generic
Pages group therefore includes `related` and unclaimed roles while a Contract
group owns `contract`, without counting that binding twice.

**Every pane list composes one row anatomy** (`PaneRow`, `PaneRows`,
`PaneLabel`, `PaneLabelAction`, `PaneLabelPopover` in `@angee/ui`): glyph or avatar, `title · detail`, a right meta, a muted
second line and an optional secondary control, on the 13/12 scale; a row is a link when its
target has a page and a button when it opens something beside it. Rows without
either remain non-interactive. `PaneRow` composes `useInAppLink` for host navigation,
preloading and native modified clicks. Reverse record pointers use
`RecordReferenceRow`, supplying the list's references so labels share one bounded
query per model without changing row order. A string `verb` describes the primary
action in its tooltip; a node is an always visible secondary action beside the
primary target. Supply `avatar` from the identity owner when a row represents a
person; the shared Avatar owns image loading and initials fallback. Day and
section labels sit above rows. Folded sections open from
their label, or from a separate chevron when `PaneLabel.labelAction` supplies a
navigable title. A pane
never picks its own font size, card or header row.

Each `timeline#kinds` child presents one kind a Python timeline kind
implements; [the kind contract](../../addons/angee/timeline/kinds.py) owns its
source models, source query, presentation and live attention predicate. The read
combines owner querysets with SQL `UNION ALL`, applying the record's and each
target owner's read policies before pagination. Published owner changes
invalidate open record reads after commit:

```ts
interface TimelineKindContent {
  label: string;
  icon: string;
  tone?: StatusIconTone;
  verb: string;
  target: { tab: string; focus?: string } | { page: true };
}
```

The rail composes lines from the union read and these presentations. Semantic
grouping (a run's decisions and changes counted on the run's line) is the
read's: kinds name a fold target, and readable events on the same record are
counted beneath lines targeting that row. Top-level lines page independently of
their children. The rail owns day separators, filtering, keyset paging, loading,
empty and error states. `target.focus`
names the focus kind (`message`, `decision`, `field`), carried in `chatterFocus`;
the target's public id travels in `chatterTarget`. A strip tab opened from a
Timeline line receives the same `ChatterSelection` as a drill-in. The back strip
is labelled with the origin tab's label. Follow subscribes to timeline kinds; the messaging
tracking subtypes that mirrored provenance and workflows are retired.

Tabs a page publishes with `useChatterContent` are runtime children of the same
container (a selection inspector on a studio or graph page is the usual case).
They follow the composed tabs and take the same narrowing. To keep the aside
off a route, narrow `record#aside` with `only: []` under `when: { route }`.
Agents use the shell's right drawer so conversation state survives navigation
independently of the record aside. See the
[Chatter owner](../../packages/ui/src/communication/Chatter.tsx).

### Resource views

`resource#views` holds the view kinds a collection's switcher offers. The
framework declares the built-in kinds under the bare ids URLs and saved views
already carry: `list`, `board`, `calendar`, `gantt` and `dashboard`. An addon
contributes a kind as a `<model>#views` child with a namespaced id. Its
`ResourceViewKindContent` carries `label` (or a ui `labelKey`), `icon`,
`capabilities` (which collection controls apply while it is active) and
`render`, a component receiving `{ resource }` that reads the collection's
filter and state through `useResourceView()`. `?view=` selects a kind by id,
and presets and favourites name it. Whether a built-in kind can run on a page
(calendar, Gantt and dashboard need declared sources) stays the kind's
capability check; whether a
kind is offered is the container's, so layers narrow kinds per model with
`only`, `except` and `hide`. See [resource view kinds](../../packages/ui/src/views/resource/resource-view-kinds.tsx).

`resource#search` has model inheritance and accepts page-declared `page.*`
extras alongside contributed typed shortcuts. `only`/`except` reach both;
`hide` alters declared contributions only, and route/app `when` narrows those
verbs. The box follows the shortcuts and is never a child that
can be hidden. Field capability checks run at composition for model
contributions; list catalogs validate page and kind-level targets at render.
See [search declarations](../../packages/ui/src/views/resource/search/shortcuts.ts).

### Rendering and testing

An owner reads its container with `useContainer(address, { models, row, impls,
extra })`: the children in order, narrowed for the current app and route, with
variants applied and `permission` checked when a `row` is given. Render-time extras with a `sequence` interleave with composed children;
unpositioned extras trail in input order, preserving page chatter tab order.
An extra's `before`/`after` may anchor on a composed child id. `only`/`except`
reach extras by id; `hide` alters declared children, so hiding an extra id fails
composition. Memoize `models` and `impls`. `ContainerOutlet` renders renderable
children in order, `containerContents` returns them as keyed nodes for a
parser, and `containerHasContent` lets the host omit an empty wrapper.
`useDrawers(edge)` reads one drawer edge; `resolveContainer` is the same
resolution outside React.

Stories and tests place children straight into the runtime with
`containersFromChildren(core, { [address]: { [id]: child } })`, with no
layering, and pass the result as the runtime's `containers` (and a
`containerScope` when a `when` rule matters). Pass the owner's declaration list
as `core` (`FORM_CONTAINERS`, `RESOURCE_CONTAINERS`, `CHATTER_CONTAINERS`,
`SHELL_CONTAINERS`) so its containers and the framework's own children exist:

```tsx
const containers = containersFromChildren(RESOURCE_CONTAINERS, {
  "notes.Note#utilities": { "notes.capture": { content: <button type="button">Capture</button> } },
});
render(<AppRuntimeProvider runtime={{ containers }}><ResourceViewUtilities value={value} /></AppRuntimeProvider>);
```

### Developer mode

Developer mode's composition dialog lists container narrowing (each layer's
`only`, `except`, `hide` and `when` per address), removed children with the
layer that removed them, and the layers behind each child's fields. The same
facts are on `createApp(...).explain.containers`; see the
[app package](../../packages/app/README.md). It also lists the menu nodes and
children absent for the session with the ref each lacks;
`createApp(...).explain.requires` maps every declared one to its ref.

## Form save contracts

`FormView` composes `SaveDiscardActions` in `ControlBand` with
`dirtyControlBandClassName`; non-record edit surfaces compose the same owners
over their own draft and persistence callbacks, with the page band outside
`SettingsShell` so it reaches the layout host.

View-as is a memory-only, read-only preview: IAM supplies the viewed identity,
real identity and permitted people. Compose `useRuntimeViewAs` at shared write
owners so permitted actions remain visible but disabled, including keyboard,
submit and upload paths. The app resets actor-bound queries on enter and exit;
change subscriptions stay closed during preview because WebSockets retain their
handshake actor. See [the provider](../../packages/app/src/providers/view-as.ts).

`FormView` treats an existing record as read-only when its resource has no update
root and the caller supplies no custom submit handler. A create-only resource can
still open a creation form. Fixtures must declare the write operations they intend
to exercise. Record locking (`readOnlyWhen`) uses the server record, not unsaved
form values.

Native form saves refresh their resource queries and authored reads of that
model; custom commands retain their mutation owner's invalidation. Declare
other affected models through `invalidateModels`; the
[shared save owner](../../packages/ui/src/views/form/use-form-view-save.ts)
resolves their resource and authored-query invalidation. A dependency between
read models is not a reason to replace generated CRUD with a custom mutation.

A save whose normalized header and line values equal the server record sends no
mutation. It resets the dirty-but-equivalent draft to those server values and
clears the dirty state, including the editable lines.

**Lines are owned parts; embedded lists show associated records.** "Lines" means
rows that exist only as part of the record and are saved with it, such as a
queue's stages or a document's lines. Declare them with `lines=HasuraLines(...)`;
the form renders them as one ordered section, saved by its one Save, with row
order as the position. `linePrimaryFields` chooses the columns shown first (the
rest wait in the data view's visible-fields menu) and `lineFields` their headers,
choices and help. `<Group label lines />` places the lines as a section; carried
by a `<model>#sections` child it narrows like any section, so a product keeps only
the lines on its route with `only` under `when`, and narrowed away they are gone.
Undeclared, the lines trail the form. A record with a life of its own (its own verbs, revision,
pages or references from elsewhere) is an associated record and stays an
embedded list, however small or ordered it is. Never split one owned collection
into several lists to protect some of its rows; lock those rows instead. See
[QueuesPage](../../addons/angee/work/web/src/views/QueuesPage.tsx).

A line row the backend locks is a system row. The child model owns the fact
(`RowLockMixin.locked_fields`), its node projects it, and the lines metadata
names it as `lockField`; [EditableLines](../../packages/ui/src/views/form/EditableLines.tsx)
reads it per row. Through the data view's `canReorderRow`, the row shows a lock
where its reorder handle would be; it carries a "System" marker, offers no duplicate
or remove, and keeps its locked cells read-only while its other cells edit. Never
re-derive a lock from a category or name in the page; the backend refuses the same
writes regardless of what the composer shows.

## Pitfalls

Hard-won traps — the wise learn from others' mistakes
([Development Guidelines](../guidelines.md)).

- **Plural copy uses native i18next suffixes:** declare `key_one`/`key_other` in the bundle and call `t("key", { count })` with a numeric count; `createNamespaceT` supplies the declared native plural defaults, including in provider-less renders. A missing category falls back to `_other`; zero uses the locale's category unless the bundle explicitly declares `_zero`.
- **A container query never matches the container itself:** a rule for `.x` inside `@container` on `.x` queries an ancestor container, usually none. Put width-dependent rules on the container's children, as [`.resource-toolbar`](../../packages/ui/src/styles/index.css) does.
- **Server preference writes are live but not transactional across tabs:** each delivered `changes()` event rebases later patches immediately, while whole-document writes already in flight can still be accepted in server order and the last accepted write wins.
- **Effect cleanup must not permanently kill a memoized resource:** StrictMode's simulated mount → cleanup → remount leaves it dead; own the resource inside the effect or explicitly re-arm it on mount, as the preference patch queue does.
- **A render callback may only read fields some column declares or the `ListView fields={[…]}` extras name:** the selection owner (`requestedFieldPaths`) fetches column-declared paths plus those extras and nothing else — an undeclared read is `undefined` on every row (a link built from it throws, a caption silently blanks). Still null-guard values a row may legitimately lack.
- **Collection state follows the surface owner.** An unrouted `List`,
  `RowsListView`, or `ResourceList` explicitly passed `presentation="embedded"`
  defaults to local `pageSize`, sorting, grouping, filtering, and view state;
  nesting alone does not infer that presentation. `DrawerResourceList` is always
  local by contract. Routed and page/workspace collections inherit their ambient
  state or own the unnamespaced route query; a page with multiple route-backed
  collections must give each an explicit state owner rather than sharing those
  keys.
- **Generated documents are an explicit prerequisite for addon checks.** Neither
  root nor package typecheck/test scripts regenerate them automatically. After a
  schema change, refresh the host's SDL and codegen before checking consumers;
  [Checks](../checks.md) owns the command order and working directories. Addon
  fragments resolve the composed stack's generated documents first. The
  repository-local `.angee/runtime` fallback may be stale in a workspace slot.
- **Optional operations travel with their owning addon.** Keep documents and their
  transport UI in the addon contributing the schema fields; a base fragment must
  codegen without optional dependents. Every agent uses the one
  [ACP runtime](../../addons/angee/agents/web/src/useAcpRuntime.ts), selecting the
  SDK version from its endpoint.
- **Agent sessions are URL selections, created on intent.** The
  [sessions page](../../addons/angee/agents/web/src/views/AgentSessionsPage.tsx)
  owns `?session=` through route search; the ACP runtime restores known/newest
  sessions and creates on first send or New session. Native Query pages own the list.
- **ACP view context follows the protocol owner.** The
  [runtime](../../addons/angee/agents/web/src/useAcpRuntime.ts) derives Current view
  attachment from the session's sent prompts: first/changed views attach automatically,
  sending consumes the badge, and users can remove or reattach it. Replayed history
  without a known normalized envelope starts unsent. V2 sends context on session
  creation and attached prompts for server rendering; v1 retains its rendered carrier.
  Native assistant-ui thread/message identities follow ACP session/message ids.
- **Relation widgets follow the SDL field kind** — a nested object FK
  (`kind:"relation"`) auto-wires to a creatable `many2one` picker; a to-one FK a
  node projects as a bare `ID` scalar auto-wires too, but as a scalar-id relation:
  the backend classifies it `kind:"scalar"`, `scalar:"ID"`, `widget:"select"` while
  keeping its `relationModelLabel`, so `relationFieldInfo` still resolves the picker
  and label (see the ID-scalar to-one pitfall below). A bare `ID` scalar with **no**
  relation target (a record's own id) stays a leaf with no widget.
- **An `ID`-scalar to-one is selected as a leaf, not an object.** Any FK a node
  projects as a bare `ID!` rather than a nested object is a scalar on the wire:
  FormView must select it *without* a sub-selection, or the detail query fails to
  build with a "must not have a selection" GraphQL error. The owner is the
  field-classification/metadata layer (`angee.graphql.data.field_classification` +
  the metadata projection): a to-one relation the node projects as a bare scalar id
  classifies as a `scalar` LEAF (so the form/detail query selects it as a scalar)
  carrying a `select` scalar-id widget and the relation target, and the frontend
  `relationFieldInfo` (`@angee/ui`'s `model-metadata-defaults`) resolves that
  scalar-id shape to the same relation picker/label as an object relation. The
  scalar-id form reads/writes the flat id; the object form reads the nested `{id}`.
- **An enum field reads UPPERCASE but writes lowercase** — a `StateField`/
  `ImplClassField` column serializes the enum *member name* on read (`GITHUB`,
  `ACTIVE`) yet its create/patch input is a `String` keyed by the lowercase
  *value* (`github`, `active`). A bare metadata-driven `select` submits the member
  name, which the String input rejects. On a create form pass `options` with
  lower-cased values (the member name is `key.upper()`, so
  `value.toLowerCase()`) and mark the field `createOnly`, so the read-side casing
  never has to round-trip back through the select. To keep the field *editable*
  instead, the `select`/`combobox` widgets reconcile the UPPERCASE read back to the
  authored option via `canonicalOptionValue` (a case-insensitive unique match), so
  lower-cased `options` round-trip correctly without `createOnly`. For status verbs
  prefer an `<Action set={{status:"disabled"}}>` over an editable status field.
  The F6 lines composer applies the same rule per cell but at the *diff boundary*,
  not with `createOnly`: `editable-lines.ts`'s `lineFieldValue` lower-cases an enum
  line cell's write (the child line input types the choices column as `String`), so
  even an untouched UPPERCASE read serializes as the lowercase model value. The
  same boundary owns the blank-cell rule (`lineToInput`): a blank non-String cell
  (`""` seed or widget-cleared `null`) is omitted on a created row so input/model
  defaults apply — Strawberry rejects `""` for Decimal/Int/Date — and ships `null`
  on an existing row (the honest "cleared" value); only a String-scalar cell's
  `""` is a real wire value and ships verbatim.
- **A to-many relation is a relation multi-select, not a `tagInput`** — a
  `kind:"list"` field that carries a relation target (an M2M, e.g. a team's
  `members` or a line's `tags`) defaults to the `many2many` widget, on record forms
  and editable lines alike. Both render it through `relationListFieldInfo` +
  `RelationMultiFieldWidget`: when editable, one field-shaped line holding the
  picked records as removable chips, an inline search over the related model
  (the multiple `Combobox` primitive) and a caret; linked chips when read-only. A
  related model whose `color` field is a colour (`recordColorField`) fills its
  chips with each record's colour, in forms and list cells alike. Inline create
  derives from the related model's metadata by the same rule as the to-one picker
  (`useRelationForms`; pass `create={null}` to decline it): an unmatched query
  offers "Create “query”", which creates the record from that name at once when
  nothing else is required (`useRelationQuickCreate`), and "Create and edit…",
  which opens the create form prefilled with it. Nothing sits beside the line
  except an authored `actionLabel` button (a lines cell, which has no search,
  keeps an icon create). Reads select the related records' identity and
  representation; writes send an array of public ids (`relationIdList`), from the
  form's `mutationData` or the lines diff. A form keeps a re-picked equal set as
  its baseline, so it is not dirty. A `kind:"list"` field with *no* relation target
  (a plain string array) stays the `tagInput`. This mirrors the to-one
  `relationFieldInfo` + `RelationFieldWidget` — compose those, never hand-roll a
  relation-list control.
- **Tags show only where an owner places them, never as a chatter tab, and save
  with the record.** An owner composes the backend's `TaggedModel` on the model
  and declares its type's `tags/tag_assignment` relation, `TaggedNode` on its
  console node and `tags_input_extensions(resource)` on its console insert and
  set inputs, then places `<Field name="tags" />` on its record form and
  `<Column field="tags" hiddenByDefault />` on its list. Resource
  metadata projects `tags` as a writable relation list to `tags.Tag`, so the form
  edits it as its standard to-many chips field: a pick or removal dirties the
  form, Save writes the id list with the row (the backend diffs the edges in the
  row's transaction) and Discard drops it, on create forms too. No tags-specific
  widget exists; the list cell keeps the shared relation-list chips.
- **Resource relation pickers support server-backed search.** Compose
  `RelationFieldWidget`; its
  [relation-options owner](../../packages/ui/src/views/relation/relation-options.ts)
  handles lazy reads, debouncing, and selected-label resolution against
  `ResourceQuery`'s executable text fields. `RelationField`/`RelationPicker`
  expose remote search through `onSearchChange` and `searchState`. Non-resource
  searches, such as host repository candidates, keep their authored-operation
  adapter with the owning addon and use Refine's query/mutation lifecycle.
- **A FormView create dialog under the console layout** needs
  `<ControlBandProvider host={undefined}>` to keep its Save band inline instead of
  portaling into the layout's band.
- **Layouts bind their own schema** (`RefineLayoutConfig.schema`): console-only fields
  need the console client — set `defaultSchema: "console"` and pin the
  public/login layout to `public`.
- **Use the shared Refine/TanStack Query data path.** Django resources and the
  operator's request/response calls use declared Refine providers. Operator
  subscriptions feed the same Query cache; raw log streams stay with their
  streaming transport. Do not introduce a second application cache/live engine.
- **react-query freshness rides invalidation, not mount-refetch.** `createApp`
  sets an app-wide `staleTime` (via refine's `reactQuery.clientConfig`, which
  layers `refetchOnWindowFocus:false` + `placeholderData:keepPreviousData`
  underneath — do not restate them), so cross-actor edits surface through the live
  provider's `changes()` subscription and mutation invalidation, not every
  remount. A model with **no** `changes()` subscription only reflects cross-actor
  edits on explicit invalidation or once `staleTime` expires; a query that must be
  always-fresh sets its own per-hook `queryOptions`, not a new app default.
- **Route code-splitting touches three things.** (1) `defaultPendingComponent` is
  the *app-wide* pending surface — it renders for every non-root match while its
  chunk loads, and (after `defaultPendingMs`) for any future `loader`-bearing
  route, not just lazy pages. Intent preloading runs each matched route's
  `beforeLoad` on hover (an already-active ancestor with the current search), so a
  `beforeLoad` only reads and redirects; session side effects belong to a router
  event, as `?debug` does with `onBeforeLoad`. A future `loader` would also run on
  hover, at most once per `defaultPreloadStaleTime`. (2) The addon-index imports in `runtime/web/app.ts`
  stay eager — manifests compose synchronously; only each manifest's *page*
  imports go through `lazyRouteComponent`. (3) A test that renders a routed page
  *through the router* (`createApp`/`RouterProvider`) must await the lazy boundary
  (`findBy*`); a test that imports the page component directly is unaffected, and a
  manifest assertion (`component` is a function) still holds for a lazy component.
  Project TypeScript configs must allow importing `.ts`/`.tsx` extensions because
  the generated runtime imports addon index source files by their package export
  paths.
- **Generated schema metadata loads before app composition.** The codegen-owned
  `loadComposedSchemas()` fetches metadata JSON assets in parallel. The host
  passes it to `@angee/app`'s `bootApp`, which shows the shared loading state and
  retries fetch failures before calling synchronous `createApp`. Addon manifests
  still compose synchronously; errors from metadata validation or route creation
  propagate as programming errors. The `@angee/app/vite` config preloads the
  metadata assets in built HTML.
- **Generate operator types from the daemon-owned SDL.** The operator's
  [addon manifest](../../addons/angee/operator/addon.toml) contributes the
  committed SDL and daemon document glob to the shared codegen pass. Do not
  hand-author daemon result types; actions return `MutationResult{status}`.
- **Expose every addon web package through the composed web manifest** — the
  composer emits `runtime/web/tailwind.sources.css` from declared package
  sources, resolving workspace links to real directories so the dev watcher sees
  edits. Do not hand-edit runtime CSS; a package missing from the manifest will
  miss its unique arbitrary Tailwind classes.
- **Shared/generic icon glyphs live in the base `chrome/icon-registry.ts`** —
  composition is fail-fast on id, so an addon cannot re-register another's glyph,
  **and adding a name to `baseIcons` collides with any addon already contributing
  it** (base composes first). This throws only at app boot — `typecheck`/`build`
  miss it — so verify the full composed app still boots after touching `baseIcons`
  or an addon's `icons`; `tsc` alone cannot catch the collision. Use the browser
  verification path in [Checks](../checks.md).
- **A new web package must enter the composed manifest and dependency graph.**
  Declare it through the addon/template owners, refresh the stack-owned install
  and generated composition, and restart the frontend through the operator
  (`angee --root "$ANGEE_ROOT" restart frontend`, or the whole-application
  restart after an install; see
  [Restart the running stack](../howto/getstarted.md#restart-the-running-stack))
  so Vite sees the new package. See [Checks](../checks.md) for environment setup
  and safe script invocation.
- **Install JS dependencies once at the owning stack workspace root.** Never run
  `pnpm install` inside a source slot, `packages/`, `addons/`, or `examples/`: a
  nested install forks linked
  `vite`/`vitest` identities and can produce `Excessive stack depth comparing
  types 'UserConfig' and 'UserConfig'` in `vitest.shared.ts`. Fix that environment
  by removing the nested package `node_modules` and reinstalling at the root;
  do not weaken the types. Every workspace package must resolve through the one
  root virtual store.
- **Prebundled `@angee/*` source edits are cache-busted by source signature, not
  a manual wipe.** A project that consumes `@angee/*` as installed packages
  (`prebundleAngeePackages: true`) prebundles them; Vite's optimizer hash comes
  from the lockfile + manifests, never package source, so a workspace edit to a
  linked `@angee/*` package (same version) would otherwise be served stale (the
  slice-1 live-verify trap). `defineAngeeWebViteConfig` (`@angee/app/vite`) hashes
  each package's on-disk source mtimes and sets `optimizeDeps.force` when it
  changed vs a persisted marker — a source edit re-optimizes, an unchanged tree
  stays cached. The in-repo example excludes `@angee/*` (linked source, HMR) so
  this never applies there.
- **Vendored third-party UI records its provenance beside the code.** Keep the
  upstream license and an `UPSTREAM.md` naming the pinned source, original file
  hashes and every local adaptation, and list both in the package's published
  `files`. Adapt primitives, glyphs, tokens and types; leave upstream algorithms
  intact. The [ReUI Gantt](../../packages/ui/src/views/gantt/UPSTREAM.md) is the
  reference.
- **Start new addon web packages from `templates/addons/web`.** The Copier template
  owns the current ceremony: `defineBaseAddon`, `resourcePageRoutes`, lazy routed
  pages, `createNamespaceT`, `expectValidBaseAddon`, and package/test wiring.
  Don't copy an older addon and then manually chase drift.
- **Architecture guardrails are tests, not review folklore.** After changing a
  framework package edge, addon package dependency, or newly exported shared owner,
  update `packages/app/src/architecture-guardrails.test.ts` deliberately. It owns
  both the package tree and the explicit addon/example roots. Record the intended
  edge or public-export allowance; do not bypass it with an undeclared import or
  a second local implementation.
- **`ResourceList` owns the record surface.** Use a `<Form>` child or `formFields`
  for generated forms, including read-only records; use `renderRecord` for a
  domain-owned transcript or detail surface. An all-read-only form never assembles
  an update mutation. Delete affordances are
  schema-capability gated: if the resource has no `delete` root, `ResourceList`/`ListView`
  omit record and bulk delete instead of requiring a delete-only `crud(...)`.
- **An addon contributes one menu root.** The app rail renders apps and their
  included, non-flattened sub-apps, at most two levels. The selected app's own
  items live in [`AppMenu`](../../packages/ui/src/chrome/AppMenu.tsx) in the top
  bar; deeper items use the shared dropdown menu and labelled groups. The bar
  leads with the app's name (or Settings) as a title, set apart from its menus
  and never marked current. The breadcrumb strip under the bar appears only for
  nested navigation, a record and deeper
  ([`useNestedBreadcrumbItems`](../../packages/ui/src/chrome/Breadcrumb.tsx)):
  a menu destination is already named by the bar, so the trail starts at the
  current menu page, with the list's return link.
  The app menu never scrolls: [`useOverflowCount`](../../packages/ui/src/lib/use-overflow-count.ts)
  measures the ordered menus followed by developer removed markers, and excess
  entries go into More. The current trail's menu keeps the last visible slot;
  when no entries fit, More holds them all and is marked current when it holds
  the current page. A route-less menu with one child is the same link in the row
  and in More. Icon-only rail links show supplementary name tooltips; developer
  descriptions follow the name. Both rail modes compose
  [`RailSortable`](../../packages/ui/src/chrome/RailSortable.tsx) for the app
  roots: dragging a root's link or Alt+Arrow reorders the one persisted rail
  order, a long press makes it the default app, and the root's tooltip gives
  that hint. The Settings, single-app and drawer trees stay plain.
  [`ChromeMenuNode`](../../packages/ui/src/chrome/menu-tree.ts) owns `isApp`,
  `appChildren()` and `menuItems()`. A node with `group:"platform"` at any depth
  contributes to the shared **Settings place** in every console: the rail and
  chooser expose one synthetic Settings entry, and the expanded rail swaps to
  the platform tree with a back header. Settings and the expansion toggle sit
  below the scrolling list, and the rail is viewport-sticky so both remain
  reachable. The expanded desktop
  header also composes the same expansion toggle, and opening the rail's app
  chooser expands a collapsed desktop rail. At desktop widths, a
  plain second activation of a nav link that already points at the current
  page toggles expansion. When the viewport fits only the icon rail, activating
  a root with visible included apps opens those sub-apps temporarily in the
  shell's shared navigation drawer; leaf apps and sub-apps navigate directly.
  Settings opens its platform roots in that drawer. Mobile uses it through the
  top-bar navigation button. Temporary navigation never changes the desktop expansion
  preference (`railLinkToggleProps` in `chrome/app-rail-model.ts` owns link
  activation; modified clicks keep the browser default). Workbench primary
  panes are reserved for page-published explorers; `TopMenuTabs` is reserved
  for explicit collection-view state, not derived menu children.
  `ChromeMenuNode.isApp` identifies non-platform roots and included apps.
  [`MenuTree.railMenuItems()`](../../packages/ui/src/chrome/menu-tree.ts) lists
  every root, or with an app selected the rail `MenuTree.confineTo(rootIds)`
  projects; the rail filters platform roots, anchors and hidden nodes. An
  included app receives compiler-emitted `app: true`; authors do not declare
  that field. A branded single-root rail shows the brand and included apps
  instead of the app chooser. Addons rearrange
  other addons' menus only through the `menus` dict's declared verbs (include,
  flatten, remove, hide, only, position), along their dependencies; see
  [`compileMenus`](../../packages/app/src/menus.ts). Never re-declare or copy
  another addon's items or routes.
  `route.menu` identifies a route's owning item when references are ambiguous.
  Multiple references do not throw; without an anchor they provide no
  menu-derived trail or metadata, and references from different roots leave the
  route with no owning root; anchor it with `route.menu`, which a root's `home`
  on such a route requires. To give another app its own copy of a page, mount
  it. `useChromePlace()` shares one memoized
  `MenuTree.match(pathname, searchStr, includeHidden, activeMenuId)` across the rail and top bar.
  Chrome currently selects the nearest visible app on that match's
  trail, and every place reader (rail, top bar, Settings-only notices) asks
  `useChromePlace().railPlace`, never a bare path.
- **A page renders in the place of the menu node it declares.** A route's
  `route.menu` anchor (inherited by record children) wins over another item's
  path prefix, even when the anchor's own target lies elsewhere. Only the
  page's own destinations beat its anchor: an item targeting its exact path, as
  a more specific preset or parameterized target. Then rank by path length,
  search params, sitting at or under the anchor, depth and pre-order; an anchor
  on an app root keeps the page on its own item there. The route projection
  publishes the winning `activeMenuId`; the Refine router binding uses that same
  destination for native breadcrumbs. Breadcrumbs occupy the sheet strip below
  the top bar; pane toggles stay in the top bar. Explorers whose selected records
  live in route search publish their ancestry with `useBreadcrumbTrail`; they
  use the shell's existing breadcrumbs rather than adding Back/Open navigation
  to the record toolbar. A route whose anchor and path
  fall in different places (an app and Settings, as when composition lifts the
  page its path nests under into Settings) fails composition
  ([`AppRouteProjection`](../../packages/app/src/resource-projection.ts)),
  naming the route, its anchor and the item its path nests under: give the
  route a path under its anchor's. Operational pages keep their app's chrome;
  configuration stays in Settings, reached from the gear or an explicit link on
  the operational page that composes the resource's record href (the work
  queues' "Queue settings", which the PM suite calls "Team settings").
- **An addon composes other addons under its own app by absorbing or
  borrowing.** Absorb with `include`: the included app moves under the
  composing node and leaves its place on the rail (the PM suite). Borrow with
  `mount`: the composing addon declares its own node,
  `"x.people": { parent: "x", mount: "iam.users", path: "people" }`, and
  [`mountRoutes`](../../packages/app/src/mounts.ts) emits an alias route named
  after it (`x.people`, plus `x.people.record` for the mounted route's record
  child) at its app's `path` joined with the node's. The app is the node's
  nearest ancestor declared at the top of the rail, so a mount keeps its path
  when another addon includes that app; an app's `path` defaults to `/<app id>`,
  since its own target may itself be a mount. The alias reuses the mounted route's
  lazy page, detail page, layout and model (as `recordModel`, so the canonical
  claim stays with its owner); the source app keeps its own page. A node targets
  one of `route` (a link), `mount` (a borrowed page) and `to` (an external URL),
  and an addon mounts only routes of addons it depends on. The alias is a real
  route anchored to its node, so `useActiveRoute`, `when: { route }`, route
  vocabulary and the menu trail are the borrowing app's. Inside the alias
  family `useRouteHref()` composes [`aliasRouteHref`](../../packages/ui/src/runtime/route-href.ts):
  the mounted route's names build the alias's (`iam.users` → `x.people`), so the
  borrowed page's own links stay in the borrowing app. The node's
  `defaultResourceView` and `recordMatch` go onto the alias.
- **Record ownership has one order.** A record link opens at the `recordMatch`
  claim its row matches, from every app; otherwise at the active app's own
  claim (with an app selected, each rail root claims its own routes);
  otherwise at the canonical route
  ([`AppRouteProjection.resourceRoutes`](../../packages/app/src/resource-projection.ts)).
  Two claims on one resource with the same field and value fail at boot; a row
  two different claims match fails where it renders. Lists read every claimed
  field (`useResourceRecordMatchFields`) so the row can choose.
- **Words and `when: { app }` follow the whole app trail.** A page's trail
  holds its root and every included app on the way, flattened ones too.
  Vocabulary composes the scopes of every app on it, outermost first, then route
  scopes, and a vocabulary `app` may name any of those apps; a container
  condition's `app` matches any of them.
- **Keep the navigation accordion and selectable ARIA tree distinct.**
  `AppRailTree` owns app-chrome parent activation, expansion, routing, and
  temporary-drawer behavior. `ui/tree.tsx` owns selectable-tree keyboard
  semantics and selection state. Keep both; never replace either with a private
  approximation of the other.
- **A group names the resource's canonical query axis.** `ResourceQuery` owns
  the translation from that axis to relation identity, label selections, server
  inputs and bucket keys. Never derive those transport names from casing or
  display labels. Use the resource query's advertised axes; the
  [query owner](../../packages/metadata/src/query.ts) and its
  [identity/label tests](../../packages/metadata/src/query.test.ts) define the
  contract.
- **Live cross-actor refresh requires a `changes()` subscription.** A list/picker
  auto-invalidates from `<model>Changed` on the subscription schema, gated on the
  schema actually declaring it — so a model without
  `changes(Model, field="<model>Changed")` in its `schema.py` refreshes on local
  writes only (no live push, no error). Add the subscription to opt a model into
  live updates; omit it and you simply get local-write invalidation.
- **A `createDefaults` seed submits on create even when `readOnly`.** `ResourceList`'s
  `createDefaults` seeds the create form, and `form-view-model.ts`'s `mutationData` submits a create
  seed even for a `readOnly`/`createOnly` field — whether the seed is the field's own
  `defaultValue` or a page-level `createDefaults` entry — so a seeded read-only field is
  no longer silently dropped from the create payload. Prefer `createOnly` (editable on
  create carrying the seed, locked on edit) when the value should stay visible-but-fixed;
  `readOnly` + `createDefaults` also works for a value the form never renders editable.
  (`editOnly` fields stay excluded on create.)
- **A storybook `meta.args`/`argTypes` is dead only if no story consumes it.** A
  bare `export const X: Story = {}` (or a `render: (args) => …`) AUTO-RENDERS from
  `meta.args` — those args are live; only a file whose every story is a zero-param
  `render: () => …` has dead meta args. Removing them when `meta.component` has a
  required prop breaks `StoryObj<typeof meta>` (it still demands the arg) — type the
  self-rendering stories as bare `StoryObj` (keep `component:` for autodocs). A
  data-bound view story declares `parameters.angeeFixture` with its schemas,
  resources, runtime and operation documents; the preview mounts the shared
  [`RuntimeFixture`](../../packages/storybook/src/stories/runtime-fixtures.tsx).
  Use `storySchema(fetch)` and `jsonResponse` for transport fixtures. Routed
  fixtures reused by tests set `angeeOwnRuntime: true` and pass
  `appearance={storyAppearance(context.globals)}` into the same owner. Each
  story has one provider stack, including appearance, data, toasts and modals.
- **`Workbench` (`layouts/Workbench.tsx`) is the collapsible inner-shell owner;
  `Explorer` is removed.** Every multi-pane content region (console body, storage,
  knowledge, iam schema, agents sessions) composes `Workbench` over `page/SplitPanes`
  (v4) — `primary` is the navigator pane, `children` the content, `secondary` the
  aside, with size/collapse persistence via `autoSave`. Do not
  hand-roll a fixed `grid`/`w-60` multi-pane shell or a pointer/arrow resize handle;
  the library owns sizing/collapse/persistence and Workbench owns the composition.
- **`barVariants` (`layouts/bar.ts`) owns bar chrome.** Bar height/edge/pad/tone/
  justify/text live once; `TopBar`/`BreadcrumbBar`/`ControlBand`/`PageToolbar`/
  `PageHeader`/`PageFooter`/`Statusline`/`ChatBar` compose it. Never hand-spell a
  bar's `h-*`/`px-*`/`py-*`/`border-b|t`/`bg-sheet*` again — route it through the
  recipe so the bars stay in lockstep.
- **Console side-pane controls stay local.** Primary and secondary panes isolate
  their ControlBand providers from the main host. An embedded collection opts
  into native toolbar wrapping; its band and toolbar must both grow with their
  contents. Opening a main record must not move a finder toolbar across panes.
- **Form controls `extend` `widget-control`; never re-hand-roll
  invalid/readOnly/disabled.** `widgetControlSurfaceVariants` (over the
  `interactiveSurfaceVariants` base) owns the control surface — focus ring,
  invalid, readOnly, disabled. Inputs/textarea/number-field/select/checkbox `extend`
  it (tv `extend`); a control that re-spells those states drifts from the owner.
- **`toneText(tone)` (`lib/tones.ts`) owns per-tone text color.** It is wired into
  `toneFill` so each tone's `text-*-text` literal lives once; never re-spell a
  `text-<tone>-text` map or a phantom `text-brand-text` (use `text-brand` /
  `text-brand-soft-text`). A `*-text` token is a foreground color, never a
  background — use `toneSolidBg`/`bg-<tone>` for fills.
- **One type scale, one name per size:** `text-2xs` (11), `text-12`, `text-13`,
  `text-sm` (14), `text-15`, `text-base` (16), `text-lg` (18), `text-22`, `text-28`,
  `text-34` — every step reads a public `--fs-*`/`--lh-*` theme token. Tailwind's
  `text-xs` is not bridged; 12px is `text-12`. No arbitrary `text-[Npx]`.
- **Control heights are tokens:** `h-control-xs` (28px) is the pane control — tabs,
  toggles, pane toolbar buttons, tree rows, select triggers; `h-btn-sm/md/lg` are
  26/30/36. Never a raw `h-7`/`size-7` on a control.
- **Brand-adjacent tokens derive from `--brand`:** link, focus border, brand line,
  brand tint and `--chart-1` follow a theme's brand without separate overrides; the
  rail hover and kbd-on-rail overlays and the whole `--fs/--lh` scale are public
  theme tokens (`THEME_TOKEN_NAMES`).
- **One radius scale: `rounded-N`** (the pixel-token scale `2/4/6/8/10/12`). Do not
  introduce the legacy `rounded`/`rounded-sm|md|lg|xl` aliases in new or rewritten
  markup.
- **Pane / Aside / primary-secondary / Panel — one name per concept.** A *Pane* is a
  `SplitPanes` split region; an *Aside* is the Chatter in the shell's secondary pane,
  whose tabs are `record#aside` children or page tabs published with
  `useChatterContent`; *primary*/*secondary* are the Workbench sidebars; a *Panel* is
  a content `Card`. Don't reuse one term for another's concept across components,
  props, or slots.

- **Message projections have one owner.** A peer addon that lists messages spreads
  the fragments messaging exports (sender, parts + file + mime, reaction groups,
  feed window) instead of re-authoring the selection; three copies of the same
  sub-selection already existed across messaging, nexus and posts.
- **One keyset feed hook.** Compose `useAuthoredKeysetFeed` from `@angee/refine`;
  domain adapters supply documents, scopes, live interests and presentation order.
  Native Query pages own loaded history; do not introduce a second row cache.
  Live feeds supply retained-ID revalidation; explicit remote snapshots can omit
  it and configure native freshness/refetch options on the same owner. Use its
  restart operation for a fresh snapshot, rather than remount keys or manual page
  accumulation. See the [IMAP sample preview](../../addons/angee/messaging_integrate_imap/README.md).
- **Enum casing is decided once.** `useEnumOptions` owns read/write casing; a
  caller that re-uppercases option values is working around the owner. Select its
  casing option for authored enum actions and retain lowercase CRUD inputs.
- **No `useParams(...) as {...}` casts.** Read generated typed routes directly or
  use `useRouteParam` at UI’s existing router integration for a named string param.
- **No `navigate({ search: ... as never })`.** Compose `updateRouteSearch` with
  relative router navigation; preserve unrelated flat search keys. Addons retain
  their own scope vocabulary and validation.
- **Master-detail is `ResourceList`.** A list beside its detail composes routed or
  controlled `ResourceList`, never `ListView` plus a `useState` selection and a
  snapshot lift.
- **Graph + inspector pages use the shell panes.** Navigator to the primary pane,
  inspector to the secondary pane, as `iam` schema page does; an in-content
  `SplitPanes` or `Workbench` with its own inspector is the copy. A Chatter tab's
  children render straight into the panel, which owns scroll and padding while the
  pane owns width, border and background; `RailPanel` owns the inspector card. Lay
  out a bounded graph — the selection or a capped neighbourhood — never a whole
  schema. Bound relationships as well as nodes: layout and canvas initialization
  both occupy the main thread. The [platform graph](../../addons/angee/platform/web/src/lib/explorer.ts)
  pages incident relationships while keeping every model and relationship reachable.
- **A connect action is one ceremony.** Pass the button as `MutationDialog.trigger`
  so the dialog owns open state, reset and native focus pairing. Addons retain
  vendor pairing and source selection.
- **`ActionResult` selections are a fragment**, or derived via `useActionMutation`;
  never hand-write `…Result`/`…Variables` interfaces and cast to
  `TypedDocumentNode`.

## Checks

Use [Checks](../checks.md) for package, composed-addon, distribution, architecture
and browser commands, including their working directories and prerequisites.
Run focused checks while editing and the relevant broad checks before handoff.

Run the package vitest suite — not just `tsc` and a story render, which miss
stale assertion drift. When verifying data-bound views, wait for the async query
to load before asserting. Meaningful UI changes require browser verification;
[End-to-End Testing](e2e.md) owns browser authoring and isolation expectations.

For page/addon changes, run a primitive-drift scan and explain every hit outside
`@angee/ui`. From the framework repository root:

```sh
rg -n '<table\b|<thead\b|<tbody\b|<tr\b|<td\b|<th\b|role="grid"|useReactTable|manualPagination' packages addons examples -g '*.tsx'
rg -n 'useAuthored(Query|Mutation)<|interface .*Data|interface .*Variables|fetch\([^)]*graphql|gql`' packages addons examples -g '*.ts' -g '*.tsx'
```

Include `../angee-messaging-bridges/addons` in the scan when that optional slot is
present and affected. Run the architecture guardrail when changing package
layering, public shared owners, or addon manifests.

A hit is not automatically wrong, but it must either compose the shared primitive
or identify the owning framework gap to fix first.
