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
- Route every user-facing string through i18n: `useBaseT()` in `@angee/base`,
  `use<Addon>T()` in an addon (both built on the SDK's `useNamespaceT(ns,
  fallback)`), with the English in the namespace bundle. A prop whose default is a
  label defaults to `undefined` and resolves `?? t("key")` in the body — never call
  `t()` in a default parameter. No hardcoded copy in a component.
- Every icon is a registered glyph rendered via `<Glyph name="…">` (or the
  `renderGlyph(icon)` slot adapter). No raw `lucide-react` import outside
  `chrome/icon-registry.ts`.
- Use shared page, view, form, table, widget, and shell primitives before adding
  new local state.
- Forms are declarative even when they branch: a `<Field showWhen={(values) => …}>`
  predicate (mirroring `Action.visibleWhen`) drives a discriminated form — a `kind`
  select that swaps the body — and a hidden field is never submitted. Reach for a
  custom form component only when the declarative DSL genuinely cannot express it.
- Register a model's create form once via `defineAddon`'s
  `forms: { Model: <…Field/Group children…> }`; the standard renderer uses it
  wherever that model is created, including the relation-picker inline create. Use
  it when the create input diverges from the read projection (write-only secrets,
  scalar-id pickers, a kind discriminator).
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
- **Live cross-actor refresh requires a `changes()` subscription.** A list/picker
  auto-invalidates from `<model>Changed` on the subscription schema, gated on the
  schema actually declaring it — so a model without
  `changes(Model, field="<model>Changed")` in its `schema.py` refreshes on local
  writes only (no live push, no error). Add the subscription to opt a model into
  live updates; omit it and you simply get local-write invalidation.

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
