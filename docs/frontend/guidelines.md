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
- App copy belongs to manifest `vocabulary` scopes: a menu root, optionally a
  route whose descendants inherit the override. The app validates existing
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
  schema. An opaque `JSON`-scalar value is parsed at its domain owner with a
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
  parallel local copies.
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
- **Products declare the shell.** Home, brand (`{ name, mark }`, with `mark` a
  registered glyph) and the selected perspective are `shell` facts layered
  along addon dependencies: a dependent overrides its dependencies, unrelated
  products fall back to the framework default, and the deployment's `ANGEE_UI`
  applies last. Hosts pass no product facts. Rail, login, public mark and
  document title read the brand through `useRuntimeBrand`; shell components
  hard-code no identity. See [`resolveShell`](../../packages/app/src/shell.ts).
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
  mounted by `createApp`; plain clicks route automatically and provider-less links
  stay native. Never pass `onNavigate` merely to call the router, or put a
  query-bearing href in TanStack `to`; use `navigate({ href })` or the owner's
  chrome href conversion.
- [Breadcrumb history](../../packages/ui/src/chrome/Breadcrumb.tsx) lives in TanStack
  location `state.trail`: console content links carry the current nested trail,
  earlier crumbs truncate it, browser Back restores it, and chrome navigation
  starts fresh. Menu destinations show no strip.
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
  [`angee.data.field_classification`](../../angee/data/field_classification.py)
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
  projection, route transitions, DnD policy, and domain actions. Page tests use
  `ShellPageTestProviders`, `PrimaryPaneTestHost`, and `ChatterTabsTestHost`
  from `@angee/app/testing` instead of hand-rolled shell provider wrappers.
- Chatter publishers compose by owner for their mounted lifetime. Panels mount
  lazily when first visited and then remain mounted for that record, so a shared
  `useRecordPeek` Records tab can open evidence without discarding draft input
  in another panel; unmounting the temporary peek must leave other publishers'
  tabs and composer intact. Chatter stays in the shell's right pane. Consumers
  do not mount their own chatter. Its tabs are `record#aside` and
  `<model>#aside` children, narrowed by the layers for the current app and
  route before tabs mount (see [Chatter](#chatter)); a container nobody narrows
  keeps all its children, including in a confined app. Public and sign-in
  routes belong to no app. A tab declared at `record#aside` appears only on
  record views. See the [Chatter owner](../../packages/ui/src/communication/Chatter.tsx).
- Human-in-the-loop queues use the resource page shell for filtering, grouping,
  paging, record selection, and URL state.
- **Routed page components are code-split.** In an addon manifest give each
  routed page `component: lazyRouteComponent(() => import("./views/Page"),
  "Page")` (the stack-native helper from `@tanstack/react-router`, already a
  direct addon dep) — never an eager `import { Page }` + `component: Page`, which
  pulls every page into the entry graph. The router owns the route-loading
  fallback *once*: `createApp` sets `defaultPendingComponent` (the unboxed,
  indeterminate `LoadingPanel`), which wraps every non-root match in Suspense
  inside its layout's `<Outlet/>`, so the chrome stays mounted. Do not hand-roll
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
- Implementation inspection reuses field-owned choice metadata. Platform owns
  the registered-type catalogue and source viewer; configured records and domain
  contracts stay with their addon. Render declared configuration with the
  existing FormSpec descriptors, and keep absent defaults distinct from explicit
  `false`, zero, or empty values.
- Color is two orthogonal axes (`lib/tones.ts` is the owner): `tone` (the palette
  — `neutral`/`brand`/`info`/`success`/`warning`/`danger`) × `variant`/fill
  (`solid`/`soft`/`surface`/`outline`/`ghost`). Drive recipe color through
  `toneClass(tone, fill)`; never hand-type a soft/solid tone triple, and never use
  the retired `default`/`error` names (they are `neutral`/`danger`).
- **Status → tone is owned once** by
  [`statusTone`](../../packages/ui/src/widgets/status-tones.ts). Framework defaults
  contain only neutral vocabulary. Addons contribute product values through
  `defineAddon({ statusTones })`; composition normalizes keys and rejects duplicate
  claims, including claims on framework defaults. Every React surface resolves
  tones with [`useStatusTone()`](../../packages/ui/src/widgets/use-status-tone.ts),
  which reads the current app's runtime; custom surfaces use the same hook as
  `statusBadge`, `colorDot`, and form headers.
  The pure `statusTone` resolver remains for non-React transforms with explicit
  vocabulary. An explicit `<Column tone>` map wins, then addon tones, then the shared
  convention, else `brand`. Scoped `resources.<model>.fields.<field>.tones`
  colors one column without claiming a global status word; its option label
  remains the displayed text. Bare enum columns use `statusBadge`, and scalar
  stages can declare it. A run
  state — stopped/running/error/warning — renders as `colorDot` (grey/green/red/amber);
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
  for a linked cell, compose `TextLink`/`Chip`/`MetricTile`, never a bespoke link
  class. If a shared view lacks what your case needs, extend it in `@angee/ui`
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
- A list route declares its shipped `presetIds`, `filterRow` quick filter and facet
  ids, `createAction`, and `boardCard` fields on `ResourceList`/`List` rather than
  building parallel controls. The route default preset is included automatically.
  Quick filter ids may name shipped presets or filter options. A scoped create verb
  uses a server-projected parent record for its permission; the create label comes
  from resource vocabulary. `chrome` may hide the view switcher, pager, or column
  chooser without changing query state; `chrome.heading` declares label, hint,
  and audience around the live count. A nonselectable list hides Share but keeps
  other contributed utilities.
- **Two-collection settings pages are a sanctioned family, not a double toolbar.**
  A `SettingsShell` may stack several `SettingsSection`s, each wrapping its own
  `ResourceList`/`DrawerResourceList` (integrate Templates: template sources +
  templates; storage settings: drives + backends). Each section is a *distinct*
  collection and owns its own data-controls/toolbar row — that is correct
  uniformity, not the double-toolbar defect. The defect is *two* chrome rows
  stacked over the *same* collection; one collection gets exactly one controls row.
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
  (`hasura_pydantic_resource`) for the uniform fetch + metadata + MCP surface,
  and its admin list processes client-side over the fetched set. Do not
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
  Human dates, ranges, relative times and durations come from the shared
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
  unboxed `LoadingPanel` only when the final geometry is unknowable, such as the
  router's code-split boundary. A renderer that knows its final geometry owns a
  shape-preserving skeleton built from `Skeleton` and one `SkeletonStatus`; retain
  settled content during background refresh. The renderer owns loading/error so
  callers describe only the happy path (cf. `preview/builtins.tsx` `FileText`).
- Forms are declarative even when they branch: a `<Field showWhen={(values) => …}>`
  predicate (mirroring `Action.visibleWhen`) drives a discriminated form — a `kind`
  select that swaps the body — and a hidden field is never submitted. Reach for a
  custom form component only when the declarative DSL genuinely cannot express it.
- A long form opts into tabs with `<Form layout="tabs">` (default `"stacked"`):
  each *labelled* `<Group>` becomes a tab panel, while the title/body/status and any
  ungrouped fields stay above the tab strip. It is per-form — existing stacked forms
  are untouched — and reuses the same `<Group>` declarations, so no field metadata is
  duplicated. Group your fields for the stacked layout and tabbing is one prop away.
- **The form hero precedes secondary facts.** `FormView` places its status control
  above the title and its lead body before the overview's groups. A domain-owned
  status control declares `<Field status widget="…" />` and registers its widget
  with the addon. Add `fill` when it should use the measured hero width; the
  widget receives `field.fill` and `field.containerWidth`. Do not repeat that
  state as another strip.
  Keep the one metadata-owned subtitle line and place operational fields in a
  collapsed Details group when their create-default behavior must remain available.
  Projects exports its [standard declarations](../../addons/angee/projects/README.md)
  so consumer routes compose the same forms, lists and record tabs.
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
  which evaluates the loaded record; fields omitted by a child projection are
  read from the canonical resource. Use `useRecordChromeContext()` inside
  the panel to scope an embedded `ListView` with resource filters. The model's
  Hasura resource owns filter/order/group/facet capabilities; the list owns
  controls, paging and `rowActions`, including confirmations for generated action
  callbacks. See [Integration Streams](../../addons/angee/integrate/web/src/IntegrationStreams.tsx).
  A product narrows a form's sections and verbs per app or route with `only`
  under `when` on `form#sections`, `form#actions` and `form#actions-menu`; a
  container nobody narrows keeps all its children and `only: []` keeps none.
  [FormView](../../packages/ui/src/views/form/form-view-surface.ts) selects
  required fields from the resolved children; authored fields and passive chrome
  remain host-owned.
- **Record verbs compose the shared action owner.** A verb in the toolbar is a
  `#actions` child and one in the overflow menu a `#actions-menu` child; either
  may render [RecordActionBar](../../packages/ui/src/views/form/RecordActionBar.tsx)
  with server-gated descriptors. [Record chrome](../../packages/ui/src/views/form/use-form-view-record-chrome.ts)
  carries the form's dirty/pending gate to toolbar and menu verbs. `<Action>`
  and record-verb children declare a projected `permission` when their verbs
  require one; unavailable verbs are omitted.
- **Record rails reuse form fields.** Contribute a `FormView.RailGroup` as a
  `<model>#rail` child with standard field descriptors and optional
  group/row permissions. The form selects those fields and binds them to its
  save state; unreadable rows stay absent. The rail follows the active record
  tab and stacks beneath the body in a narrow container.
- **Inline visibility controls bind a server verb.** Declare a
  `<Field name="visibility" widget="visibility" placement="title" visibilityAction={...} />`.
  The [shared widget](../../packages/ui/src/widgets/visibility.tsx) uses record
  chrome's server choices, revision and action gate; [Task fields](../../addons/angee/projects/web/src/task-actions.tsx)
  and [Answer fields](../../addons/angee/proposals/web/src/index.tsx) declare the binding.
- **Human decision subjects opt into the generic tab.** Compose
  [`decisionRecordTab()`](../../addons/angee/decisions/web/src/RecordDecisions.tsx)
  as a `<model>#sections` child under your own id (`"<addon>.decisions"`);
  [Decisions](../../addons/angee/decisions/README.md) owns frozen answers and
  subject identity, while each subject addon owns successor admission.
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
  preserves edits and offers reload. Adapt wire responses with
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
  widget that cannot accept it; compose the shared `rows` widget for decision
  forms with fixed-size tables. Decision-specific presentation is a
  `decisions#content` child built with `decisionContent(kind, Component)`, one
  per kind; the inbox owns the form and its React Hook Form context.
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
  composer not bound to a model record) before hand-rolling a `<label>` wrapper.
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
- Never poll for data freshness. Live updates ride GraphQL subscriptions through
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
  daemon's `onWorkspaceStatusChange`/`onServiceLogs`) over its own subscriptions.
  A timed `setInterval` is only ever for non-data UI motion (a carousel) or
  rotating a short-lived credential before it expires — never to re-read a
  resolver hoping it changed. If a foreign system publishes no change
  subscription, add one there rather than polling it from the client.
- Authored mutation result envelopes are decoded at the hook boundary. Pass
  `errorFrom` to `useAuthoredMutation` for `{error, error_code}` payloads; the
  hook throws before invalidating, so pages do not repeat result-error checks or
  accidentally refresh failed writes. Its native pending lifecycle includes
  result validation and awaited invalidation. Clear settled failures through
  the hook's `reset`; resetting a pending mutation detaches its observer without
  cancelling the write, so a dismissing dialog must preserve that pending state.
- Client-side gates are UX only. The server is the authorization boundary.
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
There are no per-kind constructor functions; an addon helper returns a plain
child (`recordPagesTab()`, `decisionContent(kind, Component)`). A new
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
checked by owners rendering for one record), `requiredFields` (readable fields
it consumes, which the form selects), `impl`, `variant` and `key`.

Any other key alters a child an addon this one depends on declared:
`sequence`, `before` and `after` move it (at the address it was declared at),
`remove: true` takes it out at composition, `hide: true` hides it at render and
`hide: false` shows again what a dependency hid. The framework's own children
are every addon's to adjust. Two unrelated layers setting one field fail.
[Messaging](../../addons/angee/messaging/web/src/index.tsx) replaces the
framework's placeholder chatter tabs this way:

```tsx
"record#aside": {
  "chatter.comments": { remove: true },
  "chatter.activity": { remove: true },
  "messaging.comments": recordCommentsTab({ submitKey, aliases: ["comments"] }),
  // messaging.activity, messaging.sources …
},
```

An addon declares a container of its own by naming an address on its own node,
with no children or with its own: `"messaging.channels#toolbar": {}`. Two
entry keys apply only there. `unique: "key"` makes every child carry a `key`
and fails two children with one key: `decisions#content` renders one
presentation per decision kind
([decisions](../../addons/angee/decisions/web/src/index.ts)). `models: true`
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

`when: { app, route, perspective }` limits an entry's render verbs (`only`,
`except` and `hide`) to some pages. `route` matches the route or any route
below it, `app` the active app, and `perspective` the selected perspective
while the console is confined; each takes one id or a list. An array of
entries holds conditional alternatives:

```ts
// A product layered on its dependencies.
containers: {
  "form#chrome": { only: ["iam.share-record"] },
  "shell#drawers-bottom": { only: [] },
  "record#aside": [
    { only: ["messaging.comments", "messaging.activity", "storage.files"] },
    { only: [], when: { route: "projects.my-work" } },
  ],
},
```

Children are declared unconditionally; declaring, moving or removing under
`when` fails. A container has no hide of its own: hide its children, or narrow
it with `only: []`. `hide: false` undoes a `hide`, never an `only`. The
deployment's `ANGEE_UI.containers` is a last layer that depends on every addon:
it alters and narrows, and declares nothing.

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

Owners pass the row's implementations where they render for one record; the
form's `#actions` and `#actions-menu` do.

### Framework containers

| Address | Children | Content |
|---|---|---|
| `form#sections` | `Group`, `Action` and `Tab` declarations on a record form | `ReactNode` |
| `form#rail` | `FormView.RailGroup` beside a saved record's tabs | `ReactNode` |
| `form#actions` | record verbs in a saved form's toolbar | `ReactNode` |
| `form#actions-menu` | record verbs in a saved form's overflow menu | `ReactNode` |
| `form#chrome` | passive record chrome at the toolbar's right edge | `ReactNode` |
| `resource#views` | view kinds a collection's switcher offers | `ResourceViewKindContent` |
| `resource#utilities` | collection utilities beside a resource view's toolbar | `ReactNode` |
| `record#aside` | chatter tabs | `ChatterTabContent` |
| `shell#notices` | notices below the console navigation | `ReactNode` |
| `shell#user-menu` | user-menu items between the theme item and sign-out | `ReactNode` |
| `shell#drawers-right`, `shell#drawers-bottom` | non-modal drawers docked on that edge | `DockedDrawerContent` |
| `auth.login#method` | sign-in methods on the login page | `ReactNode` |
| `auth.login#password-help` | password help on the login page | `ReactNode` |

The `form`, `resource` and `record` containers also take model addresses. The
declarations are `FORM_CONTAINERS`, `RESOURCE_CONTAINERS`, `CHATTER_CONTAINERS`
and `SHELL_CONTAINERS` in `@angee/ui` and `LOGIN_CONTAINERS` in `@angee/app`.
Addons own theirs, such as `messaging.channels#toolbar`,
`parties.overview#items`, `appearance.settings#tools`, `decisions#content`,
`decisions#origin` and `iam#access-visibility`.

### Chatter

The chatter aside's tabs are children of `record#aside` and `<model>#aside`. A
`record#aside` tab shows on record views; a model-level tab shows on that
model's pages and may narrow itself further with its content's `when`. A
`ChatterTabContent` carries `label`, `icon` and `render`, and optionally
`when`, `count`/`useCount`, `panelClassName` and `aliases`. The framework
declares placeholder `chatter.comments` and `chatter.activity` tabs until an
addon that supplies the real ones removes them, as messaging does. Tab ids are
namespaced like every child; `aliases` keeps a tab's former id working in
`?chatterTab=` links for one release.

Tabs a page publishes with `useChatterContent` are runtime children of the same
container. They follow the composed tabs and take the same narrowing. To keep
the aside off a route, narrow `record#aside` with `only: []` under
`when: { route }`. See the [Chatter owner](../../packages/ui/src/communication/Chatter.tsx).

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

### Rendering and testing

An owner reads its container with `useContainer(address, { models, row, impls,
extra })`: the children in order, narrowed for the current app, route and
perspective, with variants applied and `permission` checked when a `row` is
given. Memoize `models` and `impls`. `ContainerOutlet` renders renderable
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
[app package](../../packages/app/README.md).

## Form save contracts

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

A save whose normalized header and line values equal the server record sends no
mutation. It resets the dirty-but-equivalent draft to those server values and
clears the dirty state, including the editable lines.

## Pitfalls

Hard-won traps — the wise learn from others' mistakes
([Development Guidelines](../guidelines.md)).

- **Plural copy uses native i18next suffixes:** declare `key_one`/`key_other` in the bundle and call `t("key", { count })` with a numeric count; `createNamespaceT` supplies the declared native plural defaults, including in provider-less renders. A missing category falls back to `_other`; zero uses the locale's category unless the bundle explicitly declares `_zero`.
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
- **An M2M line cell is a relation multi-select, not a `tagInput`** — a `kind:"list"`
  child field that carries a relation target (an M2M, e.g. a line's `tags`) renders
  through `relationListFieldInfo` + `RelationMultiFieldWidget` (fetched options,
  chips) and reads/writes an array of public sqids; the diff serializes it via
  `relationIdList`. A `kind:"list"` field with *no* relation target (a plain string
  array) stays the `tagInput`. This mirrors the to-one `relationFieldInfo` +
  `RelationFieldWidget` cell — compose those, never hand-roll a lines cell.
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
  route, not just lazy pages. (2) The addon-index imports in `runtime/web/app.ts`
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
  sources. Do not hand-edit runtime CSS; a package missing from the manifest will
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
  descriptions follow the name.
  [`ChromeMenuNode`](../../packages/ui/src/chrome/menu-tree.ts) owns `isApp`,
  `appChildren()` and `menuItems()`. A node with `group:"platform"` at any depth
  contributes to the shared **Settings place** in every console: the rail and
  chooser expose one synthetic Settings entry, and the expanded rail swaps to
  the platform tree with a back header. Settings and the expansion toggle sit
  below the scrolling list, and the rail is viewport-sticky so both remain
  reachable. The expanded desktop
  header also composes the same expansion toggle. At desktop widths, a
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
  [`MenuTree.appRoots()`](../../packages/ui/src/chrome/menu-tree.ts) selects the
  rail-root candidates: explicit `appRoot` declarations win, otherwise it returns
  all roots; the rail filters platform roots, anchors and hidden nodes.
  `appRoot` on a non-root item throws. An included app drops `appRoot` and
  receives compiler-emitted `app: true`; authors do not declare that field.
  A branded single-root rail shows the brand and included apps instead of the
  app chooser. Addons rearrange
  other addons' menus only through the `menus` dict's declared verbs (include,
  flatten, remove, hide, only, position), along their dependencies; see
  [`compileMenus`](../../packages/app/src/menus.ts). Never re-declare or copy
  another addon's items.
  `route.menu` identifies a route's owning item when references are ambiguous.
  Multiple references within one root do not throw; without an anchor they
  provide no menu-derived trail or metadata. References from different roots
  still throw under a perspective; confinement does not choose an owner for
  the route. `useChromePlace()` shares one memoized
  `MenuTree.match(pathname, searchStr)` across the rail and top bar.
  Chrome currently selects the nearest visible app on that match's
  trail; route-owned active ids remain a follow-up. Breadcrumbs occupy the
  sheet strip below the top bar; pane toggles stay in the top bar.
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
  data-bound view story uses the shared `runtime-fixtures` owner (`RuntimeFixture` +
  `storySchema(fetch)` + `jsonResponse`), not a hand-rolled provider stack; global
  providers (`ToastProvider`, router, runtime, client) come from the preview
  decorator — don't nest a second one.
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
- **One radius scale: `rounded-N`** (the pixel-token scale `2/4/6/8/10/12`). Do not
  introduce the legacy `rounded`/`rounded-sm|md|lg|xl` aliases in new or rewritten
  markup.
- **Pane / Aside / primary-secondary / Panel — one name per concept.** A *Pane* is a
  `SplitPanes` split region; an *Aside* is page side content (`PageAside`); *primary*/
  *secondary* are the Workbench sidebars; a *Panel* is a content `Card`. Don't reuse
  one term for another's concept across components, props, or slots.

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
  `SplitPanes` with a hand-rolled `<aside>` is the copy. `PageAside`/`RailPanel`
  own asides.
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
