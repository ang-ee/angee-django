# Frontend Guidelines

Frontend code is TypeScript, React, and the rendered Angee experience. It owns
presentation, routes, menus, widgets, layouts, resource-view state, and
interaction.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
frontend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for frontend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. TypeScript dependency setup belongs in `package.json`,
`pnpm-workspace.yaml`, and `pnpm-lock.yaml`.

## Package Layering

The frontend workspace is a strict one-way stack. Each package owns one concern
and depends only on packages below it. `docs/stack.md` says which rented library
owns what; this section says which Angee package wraps it and who may import whom.

### Target DAG

Dependencies point down only. A package never imports a package above it.

```
rented libs   @refinedev/core · @refinedev/hasura · graphql-request/ws ·
              TanStack Router/Table · react-hook-form/zod · i18next · lucide · Base UI
   │
@angee/refine     Hasura-dialect Refine binding — zero domain/metadata knowledge
   │
@angee/metadata  metadata (angee.resources) → Refine config bridge
   │
@angee/ui         the single rendered binding + headless view-state
   │
@angee/app        composition + app shell — the only package depending on all above
   │
@angee/<domain>   addons: pages + codegen documents
```

| Package | Owns |
|---|---|
| `@angee/refine` | the parts of a Refine+Hasura app every project shares, with **zero domain/metadata knowledge**: data/transport/live providers, the router bridge, typed-document contracts, and the `dialect/` hooks (action, aggregate, groupBy, facets, deletePreview, revisions) over `useCustom`. |
| `@angee/metadata` | the **only** consumer of `angee.resources` metadata: artifact load/validate, projection to Refine `resources[]` + `meta`, the one field kind/scalar/widget classifier, group/facet/drill-down dimension specs, and per-action capabilities → accessControl. |
| `@angee/ui` | the single rendered binding + headless view-state: `views/{list,form,record,relation,visualizations}` + headless view-models, chrome (rail/topbar/breadcrumb/spotlight), widgets, feedback (toast), the Base UI primitives binding, and the `runtime/` contracts it consumes — the `AppRuntime` registry/session context + its lookup hooks, `makeContext`, and the menu/slot/preview/widget/form contribution types (the binding owns the runtime it renders against; `@angee/app` only mounts the provider). |
| `@angee/app` | assembles the app: `define-addon`, `defineBaseAddon`, `createApp`, the `providers/{auth,i18n,notification,accessControl}`, addon-route → TanStack tree routing, the slot/widget/form/preview/icon registries, and the app shell. |
| `@angee/<domain>` | a domain addon: its pages and codegen `documents*.ts`. |

### Target Decisions

- Auth, preferences, and runtime i18n are app-owned providers under
  `@angee/app/src/providers/{auth,i18n}`. Transport auth headers stay in
  `@angee/refine`; chrome reads only the app-supplied session surface on
  `@angee/ui`'s `AppRuntime`.
- Dialect data hooks live in `@angee/refine` as metadata-free hooks. Callers
  resolve `resourceOperationTarget` at the metadata edge and pass the root as
  `{ root }`.
- Runtime i18n has one app-owned i18next instance. Addon bundles are
  namespace-relative, and the rendered binding namespace is `ui`.
- Record display representation is a backend-emitted metadata fact in the
  `angee.resources` artifact. Frontend code reads that field and keeps only the
  `id` floor; it does not probe candidate display fields.
- Layering and unconsumed-owner guardrails are Vitest checks over the locked
  package graph. Do not add a boundary-lint dependency without updating
  `docs/stack.md` and the manifests together.

### Current → target

Where each concern lives **today** versus where the open refactor waves move the
remaining debt. Deleted shell packages are historical only; new code uses the
real package names below.

| Concern | Current owner | Target owner |
|---|---|---|
| Data/transport/live providers, router bridge, typed-document contracts, stable-deps | `@angee/refine` | `@angee/refine` |
| Dialect data hooks (aggregate/action/deletePreview/facets/groupBy, revisions, authored-hooks) | `@angee/ui` rendered-data hooks | `@angee/refine` dialect hooks (metadata-free; target resolved at the caller edge as `{ root }`) |
| Metadata artifact, resource projection, field classifier, dimensions, capabilities, row contracts | `@angee/metadata` | `@angee/metadata` |
| Invalidation: resource targets vs authored-query metadata | `@angee/metadata` resource targets + `@angee/ui` authored-query hooks | `@angee/metadata` resource targets + `@angee/refine` authored-query metadata |
| Rendered views / chrome / widgets / feedback / primitives | `@angee/ui` | `@angee/ui` |
| `lib/` styling helpers (cn/tv/tones/dnd) | `@angee/ui` | `@angee/ui` |
| Runtime contracts the binding consumes — the `AppRuntime` registry + its `useWidget`/`useSlot`/`usePreviews`/`useT`/`useNamespaceT` lookups, the `makeContext` factory, and the menu/slot/preview/widget/form contribution contracts | `@angee/ui` | `@angee/ui` |
| `defineAddon` / `composeAddons` (addon-manifest composition) | `@angee/app` | `@angee/app` |
| `createApp` / `defineBaseAddon` + app shell (the single `<Refine>`/cache/live owner) | `@angee/app` | `@angee/app` |
| Auth provider | `@angee/app` `providers/auth` | `@angee/app` `providers/auth` |
| i18n provider | `@angee/app` i18n provider | `@angee/app` i18n provider |

### One-way rules

These are the dependency invariants; a violation is a layering bug, not a
convenience.

- `@angee/refine` imports **only rented libs** — never `@angee/metadata`,
  `@angee/ui`, `@angee/app`, and never any `angee.resources` metadata.
- `@angee/metadata` must **NOT** import `@angee/refine`.
- `@angee/ui` may import `@angee/refine` + `@angee/metadata`, but **not**
  `@angee/app`.
- `@angee/app` is the **ONLY** package that may import compose / `createApp`-level
  concerns. The `AppRuntime` context and its lookup hooks live in **`@angee/ui`**
  (the binding owns the runtime it renders against); `@angee/app` composes the
  addon manifests (`composeAddons`) and mounts that `AppRuntimeProvider` with the
  merged value. `@angee/ui` reads it via the context — never by importing
  `@angee/app`.
- No addon imports deleted shell packages; addon web code composes the real
  framework owners directly.

### Carried debts

No shared frontend engine is intentionally carried as debt: client-side
filtering, sorting, grouping, expansion, row selection, and pagination compose
TanStack Table row models. Angee keeps only the thin lookup evaluator that lets
TanStack apply the URL-owned filter object to in-memory rows.

## Rules

- Python ships schema and operations. TypeScript ships UX.
- **Authored operations are typed, never hand-mirrored.** A bespoke (non-CRUD)
  operation is a `graphql()` document imported from `@angee/gql/<schema>`; its
  result/variables types come from the generated `TypedDocumentNode` (use
  `DocumentType<typeof Doc>` for named result types and
  `DocumentVariables<typeof Doc>` from `@angee/refine` for named variable types) —
  never a hand-written `…Data`/`…Variables` interface, and never call-site
  `<TData,TVars>` generics on the `useAuthored*` hooks. The operation's **file
  name picks its schema**: `documents.ts`/`documents.console.ts` → console,
  `documents.public.ts` → public. An op must live in a `documents*.ts` file (the
  codegen glob does not scan inline ops), and a console op placed in a
  `documents.public.ts` (or vice versa) fails codegen loudly against the wrong
  schema. Keep valibot only to narrow a `JSON`-scalar field the schema leaves
  opaque (parse, do not assert).
- **Single-id action mutations are derived, not authored.** For a
  `<field>(id: ID!): ActionResult` mutation, call
  `useActionMutation<ActionFieldName>("field")` from `@angee/ui` in headless
  rendered-view code, or
  `useRecordActionMutation<ActionFieldName>("field")` for a rendered
  `@angee/ui` `<Action run={...}>` bound to the open record. `ActionFieldName`
  comes from `@angee/gql/<schema>/actions`; no document, result type, or
  variables are authored. The shared hook owns deriving the Hasura custom
  mutation and running it through refine `useCustomMutation`; the rendered record
  adapter owns binding it to `ActionContext` (record id, refresh,
  missing-record handling, success hooks).
  Don't hand-author these as `graphql()` documents or page-local
  `ctx.record.id → mutate → refresh` callbacks.
- React does not own business logic, permissions, models, or persistence.
- **React state has one owner.** Keep canonical facts in the smallest owner:
  route/search facts in TanStack Router/nuqs, server facts in refine data hooks
  and react-query, resource-view facts in `ResourceViewProvider`, form facts in
  `@refinedev/react-hook-form`/`FormView`, and ephemeral interaction state in the
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
  described under "Package layering" below.
- Rendered resource pages use `resourcePageRoutes(name, path, component,
  resource?)` from `@angee/app`; the helper owns the list + `$id` child pair and
  the default `"console"` layout. Addon manifest tests call
  `expectValidBaseAddon(manifest)` from `@angee/app/testing` and keep only
  genuinely addon-specific assertions.
- Compose addon capabilities at build time through the manifest + `composeAddons`
  (widgets, i18n, icons, forms, slots, previews, and menu declarations); never
  register or mutate a module-global at runtime. `usePreviews`/`useWidget`/
  `useSlot` read the composed `AppRuntime`; menu declarations project into refine
  resources and chrome renders refine `useMenu`.
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
- **Routed page components are code-split.** In an addon manifest give each
  routed page `component: lazyRouteComponent(() => import("./views/Page"),
  "Page")` (the stack-native helper from `@tanstack/react-router`, already a
  direct addon dep) — never an eager `import { Page }` + `component: Page`, which
  pulls every page into the entry graph. The router owns the route-loading
  fallback *once*: `createApp` sets `defaultPendingComponent` (a `LoadingPanel`),
  which wraps every non-root match in Suspense inside its layout's `<Outlet/>`, so
  the chrome stays mounted. Do not hand-roll `React.lazy` + a manual `<Suspense>`
  around a route's `<Outlet/>`. Split only routed pages — lighter manifest content
  (slot/section content, forms, glyphs) stays eager; where a route needs a
  provider wrapper (e.g. operator's transport), wrap the `lazyRouteComponent`
  result in the thin route component, and the dynamic `import()` still splits the
  view.
- One component tree. Extend or register; do not fork.
- Slots are additive extension points. Use them before copying a component.
- Tokens beat color props and one-off variants. Theme by overriding tokens.
- Color is two orthogonal axes (`lib/tones.ts` is the owner): `tone` (the palette
  — `neutral`/`brand`/`info`/`success`/`warning`/`danger`) × `variant`/fill
  (`solid`/`soft`/`surface`/`outline`/`ghost`). Drive recipe color through
  `toneClass(tone, fill)`; never hand-type a soft/solid tone triple, and never use
  the retired `default`/`error` names (they are `neutral`/`danger`).
- **Status → tone is owned once** by the shared `STATUS_TONES` vocabulary
  (`widgets/status-tones.ts`, the domain layer over the domain-free `lib/tones.ts`).
  The `statusBadge` (pill) and `colorDot` (dot) widgets and every console status
  surface (`StateTag`) resolve a value through `statusTone(value, override?)`: an
  explicit `<Column tone>` map wins, then the shared convention, else `brand`. Never
  add a private status→tone map (the operator console kept one and drifted). A run
  state — stopped/running/error/warning — renders as `colorDot` (grey/green/red/amber);
  a value the vocabulary doesn't know takes an explicit `<Column tone>` (e.g. a task's
  `blocked`→`danger`). Keep the run state a separate field from a lifecycle/state enum
  rather than overloading one column with both axes.
- Route every user-facing string through i18n: `useUiT()` in `@angee/ui`,
  `use<Addon>T()` in an addon (created with `createNamespaceT(ns, fallback)`),
  with namespace-relative English keys in the namespace bundle. A prop whose default is a
  label defaults to `undefined` and resolves `?? t("key")` in the body — never call
  `t()` in a default parameter. No hardcoded copy in a component. Two boundaries
  stay plain English: an addon's declarative manifest menu/route `label:` (chrome
  data, not in-component copy — none are routed), and a form registered via
  `forms:` (a statically parsed element, never rendered as a component, so a hook
  cannot reach its `<Field label>`).
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
  (the owner) so every addon gets it. The principle and what a hand-rolled copy
  silently drops live in `AGENTS.md` → "Compose, never re-implement, at the addon
  level".
- **Routes and pages stay thin.** A route declares URL, layout, menu/chrome,
  refine resource, action, and component. A resource-backed page composes the
  standard resource action components with `List` and `Form` declarations; a
  daemon/remote/in-memory collection composes `RowsListView` or a named shared
  owner; a grouped or board-capable resource composes `ListView` with grouping
  and the matching backend aggregate/filter contract. Page components may add small
  action controls or hooks, but they do not own table mechanics, duplicate route
  params, cache state, bespoke loading/error surfaces, or local copies of shared
  resource-view state.
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
  renderer for the genuinely non-resource in-memory case (the operator-daemon
  quarantine).
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
  card) instead of wrapping it in a `grid place-content-center` div; `LoadingPanel`
  already self-centers. A renderer owns its own loading/error so callers describe
  only the happy path (cf. `preview/builtins.tsx` `FileText`).
- Forms are declarative even when they branch: a `<Field showWhen={(values) => …}>`
  predicate (mirroring `Action.visibleWhen`) drives a discriminated form — a `kind`
  select that swaps the body — and a hidden field is never submitted. Reach for a
  custom form component only when the declarative DSL genuinely cannot express it.
- A long form opts into tabs with `<Form layout="tabs">` (default `"stacked"`):
  each *labelled* `<Group>` becomes a tab panel, while the title/body/status and any
  ungrouped fields stay above the tab strip. It is per-form — existing stacked forms
  are untouched — and reuses the same `<Group>` declarations, so no field metadata is
  duplicated. Group your fields for the stacked layout and tabbing is one prop away.
- A relation field is a link, not a dead end. A routed collection page tags its
  refine resource on the route — `{ name, path, component, resource:
  "OAuthClient" }` (one route per resource, build-time fail-fast) — and the
  relation widget resolves it through `useResourceRoute(resource)` to show a
  "follow" arrow to the selected record's detail page (breadcrumbs come from
  refine). A resource with no routed page simply shows no arrow.
- Register a resource's create form once via `defineAddon`'s
  `forms: { Model: <…Field/Group children…> }`; the standard renderer uses it
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
- Toolbar/action dialogs with ordinary field inputs compose `MutationDialog` from
  `@angee/ui`. It owns the `DialogForm` scaffold, value reset, required gating,
  submit busy/error state, and FieldDescriptor widget rendering; addons provide
  fields, mutation variables, and domain result handling.
- A labeled control is a page element or a `FieldRoot`. Reach for `FieldRoot` /
  `FieldLabel` (the stacked label-over-control owner, e.g. for an ephemeral
  composer not bound to a model record) before hand-rolling a `<label>` wrapper.
  A native input pairs `FieldLabel htmlFor` with the control `id`; a button-trigger
  control (a `Select`) labels via `FieldLabel nativeLabel={false} render={<span/>}`
  + the control's `aria-labelledby`.
- Base exposes seams for product chrome; it does not hardcode product affordances.
  Record-level chrome (star/share/follow) is host-contributed into
  `FORM_VIEW_RECORD_CHROME_SLOT` via the manifest `slots:`; render contributions
  with the shared `SlotOutlet`.
- Never poll for data freshness. Live updates ride GraphQL subscriptions through
  refine's live provider and react-query invalidation, not a `setInterval`
  refetch loop. Opt a model into live cross-actor refresh by declaring
  `changes(Model, field="<model>Changed")` in its `schema.py`; local writes
  invalidate through refine mutations, and subscription pushes invalidate the
  affected refine resources. Stream foreign-system state (e.g. the operator
  daemon's `onWorkspaceStatusChange`/`onServiceLogs`) over its own subscriptions.
  A timed `setInterval` is only ever for non-data UI motion (a carousel) or
  rotating a short-lived credential before it expires — never to re-read a
  resolver hoping it changed. If a foreign system publishes no change
  subscription, add one there rather than polling it from the client.
- Authored mutation result envelopes are decoded at the hook boundary. Pass
  `errorFrom` to `useAuthoredMutation` for `{error, error_code}` payloads; the
  hook throws before invalidating, so pages do not repeat result-error checks or
  accidentally refresh failed writes.
- Client-side gates are UX only. The server is the authorization boundary.
- No Python view DSL, no frontend metadata hidden in backend decorators.

## Pitfalls

Hard-won traps — the wise learn from others' mistakes (`docs/guidelines.md`).

- **A filtered `pnpm typecheck`/`test` skips the root `pretypecheck: codegen` hook.**
  The root `typecheck`/`test` scripts run `pnpm codegen` first; `pnpm --filter <pkg>
  typecheck` (and filtered vitest) does not. After any SDL change it then runs against
  stale generated `@angee/gql` types and fails with spurious `Cannot find module
  '@angee/gql/console'` or implicit-`any` errors in `documents.ts` consumers — not real
  defects. After a schema change, regenerate in order: `manage.py schema` (the SDL — see
  the backend "Regenerate the SDL after `angee build`" pitfall) → `pnpm codegen` → then the
  filtered typecheck/test.
- **Relation widgets follow the SDL field kind** — a nested object FK
  (`kind:"relation"`) auto-wires to a creatable `many2one` picker; a bare `ID`
  scalar (`kind:"scalar"`) is not auto-detected and must use `widget:"select"`
  (`many2one` selects `<field>.id`, invalid on a scalar id).
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
- **A server-backed typeahead is not a `RelationField`** — `RelationField`/
  `RelationPicker` own their query state and filter a fixed `options` list
  client-side, so they cannot drive a remote search. For one (e.g. a host repo
  search), build a thin control on the dialog/`Input` primitives whose debounced
  query feeds `@angee/ui`'s refine-backed `useAuthoredQuery`, and run the write
  through `useAuthoredMutation(..., { invalidateModels: [...] })` or the
  matching refine invalidation owner after the write.
- **A FormView create dialog under the console layout** needs
  `<ControlBandProvider host={undefined}>` to keep its Save band inline instead of
  portaling into the layout's band.
- **Layouts bind their own schema** (`RefineLayoutConfig.schema`): console-only fields
  need the console client — set `defaultSchema: "console"` and pin the
  public/login layout to `public`.
- **Keep urql out of app data paths.** The only remaining urql owner is the
  operator daemon quarantine. Django-backed app resources use refine data hooks,
  react-query invalidation, and the Hasura provider; do not reintroduce a
  second app cache/live engine.
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
- **Generate the operator console's types from the Go daemon's introspected SDL**
  (`operator_schema` → codegen), never by hand; daemon actions return
  `MutationResult{status}`, not `{ok}`.
- **Expose every addon web package through the composed web manifest** — the
  composer emits `runtime/web/tailwind.sources.css` from declared package
  sources. Do not hand-edit runtime CSS; a package missing from the manifest will
  miss its unique arbitrary Tailwind classes.
- **Shared/generic icon glyphs live in the base `chrome/icon-registry.ts`** —
  composition is fail-fast on id, so an addon cannot re-register another's glyph,
  **and adding a name to `baseIcons` collides with any addon already contributing
  it** (base composes first). This throws only at app boot — `typecheck`/`build`
  miss it — so the full addon set is composed in
  `examples/notes-angee/web/src/addon-composition.test.tsx`; run `pnpm run test`
  (not just `tsc`) after touching `baseIcons` or an addon's `icons`.
- **A new web package needs `pnpm install` + a Vite restart** (Vite snapshots
  workspace packages at start) plus registration in the host `main.tsx` addons and
  `package.json`.
- **Start new addon web packages from `templates/addons/web`.** The Copier template
  owns the current ceremony: `defineBaseAddon`, `resourcePageRoutes`, lazy routed
  pages, `createNamespaceT`, `expectValidBaseAddon`, and package/test wiring.
  Don't copy an older addon and then manually chase drift.
- **Architecture guardrails are tests, not review folklore.** After changing a
  framework package edge, addon package dependency, or newly exported shared owner,
  update `angee/web/app/src/architecture-guardrails.test.ts` with the intended
  package edge or deliberate public-export allowance; do not bypass it with an
  undeclared import or a second local implementation.
- **`ResourceList` still needs a form declaration, even for read-only records.** Give
  discovered/read-only resources a `<Form>` child or `formFields` with read-only fields;
  an all-read-only form never assembles an update mutation. Delete affordances are
  schema-capability gated: if the resource has no `delete` root, `ResourceList`/`ListView`
  omit record and bulk delete instead of requiring a delete-only `crud(...)`.
- **An addon contributes one rail (app) root** (`group:"platform"`); its children
  are the top-bar menus, and a child that itself has children renders as a dropdown.
  A route referenced by more than one menu item must set `route.menu` (the owning
  item's id) or the chrome derivation throws "referenced by multiple menu items" —
  or make the root route-less so it inherits its target through a descendant and the
  leaf is the route's sole reference.
- **Group by a to-one relation with the camel group-key field.** A server group-by
  axis may traverse a forward FK/OneToOne (e.g. `group_by_fields=["oauth_client__is_enabled"]`
  in `schema.py`; to-many stays refused). The backend emits the group-key field in
  camel form (`oauthClient_IsEnabled`) and the groupable enum in `__` SNAKE_UPPER
  (`OAUTH_CLIENT__IS_ENABLED`). A `ResourceToolbarGroupOption`'s `group.field` is the
  *camel key* (`"oauthClient_IsEnabled"`) — `resourceViewGroupToAggregateDimension`
  reads it verbatim as the bucket key and `fieldToSnake`-uppercases it to the enum
  (a `_<Capital>` restores the Django `__`). Use the camel key, not the snake path.
- **Live cross-actor refresh requires a `changes()` subscription.** A list/picker
  auto-invalidates from `<model>Changed` on the subscription schema, gated on the
  schema actually declaring it — so a model without
  `changes(Model, field="<model>Changed")` in its `schema.py` refreshes on local
  writes only (no live push, no error). Add the subscription to opt a model into
  live updates; omit it and you simply get local-write invalidation.
- **`createDefaults` needs a submittable field, never `readOnly`.** `ResourceList`'s
  `createDefaults` seeds the create form, but `FormView.mutationData` drops every
  `readOnly` field from the payload — so a `readOnly` field pinned by `createDefaults`
  is silently *not* sent, failing a required create input. Use `createOnly` (editable
  on create carrying the seed, locked on edit) or a plain field; reserve `readOnly`
  for values the create input does not accept.
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
  justify/text live once; `TopBar`/`Breadcrumb`/`ControlBand`/`PageToolbar`/
  `PageHeader`/`PageFooter`/`Statusline`/`ChatBar` compose it. Never hand-spell a
  bar's `h-*`/`px-*`/`py-*`/`border-b|t`/`bg-sheet*` again — route it through the
  recipe so the bars stay in lockstep.
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
- **Layout slot ids use the `@angee/ui.*` symbol namespace.** Register new slots as
  `Symbol.for("@angee/ui.<name>-slot")` (see `layouts/slots.ts`); the legacy
  rendered-binding prefix is retired.

## Checks

Run package-scoped commands while editing, then the broad checks before handoff:

```sh
pnpm run typecheck
pnpm run test
pnpm run build
```

Run the package vitest suite — not just `tsc` and a story render, which miss
stale assertion drift. When verifying data-bound views, wait for the async query
to load before asserting. Use browser verification for meaningful UI changes.

For page/addon changes, run a primitive-drift scan and explain every hit outside
`@angee/ui`:

```sh
rg -n '<table\b|<thead\b|<tbody\b|<tr\b|<td\b|<th\b|role="grid"|useReactTable|manualPagination' angee/web addons examples/notes-angee/web -g '*.tsx'
rg -n 'useAuthored(Query|Mutation)<|interface .*Data|interface .*Variables|fetch\([^)]*graphql|gql`' angee/web addons examples/notes-angee/web packages -g '*.ts' -g '*.tsx'
```

Run the architecture guardrail when changing package layering, public shared
owners, or addon manifests:

```sh
pnpm --filter @angee/app run test -- architecture-guardrails.test.ts
```

A hit is not automatically wrong, but it must either compose the shared primitive
or identify the owning framework gap to fix first.
