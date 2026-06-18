# Frontend Guidelines

Frontend code is TypeScript, React, and the rendered Angee experience. It owns
presentation, routes, menus, widgets, shells, view state, and interaction.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
frontend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for frontend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. TypeScript dependency setup belongs in `package.json`,
`pnpm-workspace.yaml`, and `pnpm-lock.yaml`.

## Rules

- Python ships schema and operations. TypeScript ships UX.
- **Authored operations are typed, never hand-mirrored.** A bespoke (non-CRUD)
  operation is a `graphql()` document imported from `@angee/gql/<schema>`; its
  result/variables types come from the generated `TypedDocumentNode` (use
  `DocumentType<typeof Doc>` for named result types and
  `DocumentVariables<typeof Doc>` from `@angee/sdk` for named variable types) —
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
  `useActionMutation<ActionFieldName>("field")` (`ActionFieldName` from
  `@angee/gql/<schema>/actions`) — no document, result type, or variables. The
  runner takes the `id` and returns the success message (it applies
  `runActionResult`: an `ok:false` business failure throws → error toast). Don't
  hand-author these as `graphql()` documents.
- React does not own business logic, permissions, models, or persistence.
- Use `defineAddon` (headless, `@angee/sdk`) or `defineBaseAddon` (rendered,
  `@angee/base`, routes carry React components) to declare an addon, and
  `createApp` for the project's host composition. One greppable seam per addon —
  never annotate a bare `const x: BaseAddon = {…}`.
- Compose addon capabilities at build time through the manifest + `composeAddons`
  (widgets, i18n, icons, forms, slots, previews); never register or mutate a
  module-global at runtime. `usePreviews`/`useWidget`/`useMenus`/`useSlot` read the
  composed `AppRuntime`.
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
- Route every user-facing string through i18n: `useBaseT()` in `@angee/base`,
  `use<Addon>T()` in an addon (both built on the SDK's `useNamespaceT(ns,
  fallback)`), with the English in the namespace bundle. A prop whose default is a
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
  by rendering them.
- Use shared page, view, form, table, widget, and shell primitives before adding
  new local state. Never hand-roll a data view (grid/list/group/board), form, or
  detail in an addon — compose the shared views (`ListView`/`RowsListView`/
  `DataPage`/`RecordView`) and record fragments (`RecordHeader`/`MetaGrid`/
  `MetricStrip`); for a linked cell, compose `TextLink`/`Chip`/`MetricTile`, never
  a bespoke link class. If a shared view lacks what your case needs, extend it in
  `@angee/base` (the owner) so every addon gets it. The principle and what a
  hand-rolled copy silently drops live in `AGENTS.md` → "Compose, never
  re-implement, at the addon level".
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
  model on the route — `{ name, path, component, model: "OAuthClient" }` (one route
  per model, build-time fail-fast) — and the relation widget resolves it through
  `useModelRoute(model)` to show a "follow" arrow to the selected record's detail
  page (breadcrumbs come from that route). A model with no routed page simply shows
  no arrow.
- Register a model's create form once via `defineAddon`'s
  `forms: { Model: <…Field/Group children…> }`; the standard renderer uses it
  wherever that model is created, including the relation-picker inline create. Use
  it when the create input diverges from the read projection (write-only secrets,
  scalar-id pickers, a kind discriminator). With a registered form,
  `RelationPicker`'s `create` needs only `{ model }` (the override supersedes any
  passed `fields` on create); pass inline `fields` only for a data-dependent form
  whose options are fetched at runtime and so cannot be a static registration.
  `RelationPicker` also offers inline **edit** (a pencil beside the picker opens the
  *selected* record in a form dialog) — wired by `RelationFieldWidget` from the
  related model's fields, so a relation is created, edited, and followed without
  leaving the parent form. The create-form override stays create-only: an edit
  dialog renders the passed `fields` (the registered form is not reused for edit).
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
- Never poll for data freshness. Live updates ride GraphQL subscriptions and the
  normalized cache, not a `setInterval` refetch loop. Opt a model into live
  cross-actor refresh by declaring `changes(Model, field="<model>Changed")` in its
  `schema.py` — the relay invalidator then auto-subscribes and refetches every
  registered read (`useResourceRecord`/list/picker) on each push; a model without
  it still invalidates on local writes. Stream foreign-system state (e.g. the
  operator daemon's `onWorkspaceStatusChange`/`onServiceLogs`) over its own
  subscriptions. A timed `setInterval` is only ever for non-data UI motion (a
  carousel) or rotating a short-lived credential before it expires — never to
  re-read a resolver hoping it changed. If a foreign system publishes no change
  subscription, add one there rather than polling it from the client.
- Client-side gates are UX only. The server is the authorization boundary.
- No Python view DSL, no frontend metadata hidden in backend decorators.

## Pitfalls

Hard-won traps — the wise learn from others' mistakes (`docs/guidelines.md`).

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
  never has to round-trip back through the select. For status verbs prefer an
  `<Action set={{status:"disabled"}}>` over an editable status field.
- **A server-backed typeahead is not a `RelationField`** — `RelationField`/
  `RelationPicker` own their query state and filter a fixed `options` list
  client-side, so they cannot drive a remote search. For one (e.g. a host repo
  search), build a thin control on the dialog/`Input` primitives whose debounced
  query feeds `useAuthoredQuery`, and refresh the affected list with
  `useModelInvalidation(model)` after the write.
- **A FormView create dialog under the console shell** needs
  `<ControlBandProvider host={undefined}>` to keep its Save band inline instead of
  portaling into the shell's band.
- **Shells bind their own schema** (`ShellConfig.schema`): console-only fields
  need the console client — set `defaultSchema: "console"` and pin the
  public/login shell to `public`.
- **Order urql exchanges `[cache, subscription, fetch]`** — a client missing
  `fetchExchange` forces plain queries over graphql-ws and they hang; never set a
  poll interval shorter than the slowest resolver, or a `network-only` reexecute
  aborts the in-flight request.
- **Generate the operator console's types from the Go daemon's introspected SDL**
  (`operator_schema` → codegen), never by hand; daemon actions return
  `MutationResult{status}`, not `{ok}`.
- **Add every new addon web package to the app CSS `@source`** — its unique
  arbitrary Tailwind classes silently fail to generate otherwise.
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
- **`DataPage`/`ListView` require a `delete` root field and a form — even
  read-only.** Both wire `useBulkDelete` eagerly (needs `delete<Model>`) and
  resolve form fields eagerly (needs a `<Form>` child or `formFields`); there is no
  read-only mode. A discovered/read-only model (one synced from a source, never
  hand-created) still needs delete-only `crud(...)` (matching integrate's
  `Repository`), and to stay view-only it must pass **no** `routed`/`onSelect` — then
  records never open, so the `update`/`create` roots are never assembled.
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
  (`OAUTH_CLIENT__IS_ENABLED`). A `DataToolbarGroupOption`'s `group.field` is the
  *camel key* (`"oauthClient_IsEnabled"`) — `dataViewGroupToAggregateDimension`
  reads it verbatim as the bucket key and `fieldToSnake`-uppercases it to the enum
  (a `_<Capital>` restores the Django `__`). Use the camel key, not the snake path.
- **Live cross-actor refresh requires a `changes()` subscription.** A list/picker
  auto-invalidates from `<model>Changed` on the subscription schema, gated on the
  schema actually declaring it — so a model without
  `changes(Model, field="<model>Changed")` in its `schema.py` refreshes on local
  writes only (no live push, no error). Add the subscription to opt a model into
  live updates; omit it and you simply get local-write invalidation.
- **`createDefaults` needs a submittable field, never `readOnly`.** `DataPage`'s
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
