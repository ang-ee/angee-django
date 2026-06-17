# Integrations form & relation UX improvements

## Context

The `integrate`/OIDC console forms have grown long and flat, and relation fields
are dead-ends. Three concrete needs (all framework-level, so every consumer addon
inherits them):

1. **Long forms should tab.** The OAuth-client provider form
   (`/integrate/providers/$id`) already declares 6 logical `<Group>` sections
   (client, endpoints, behavior, scopes, claims, oauth-metadata) but renders them
   as one long stacked column. They should render as tabs.
2. **Relation selects are dead-ends.** A `many2one` dropdown (e.g. an OIDC
   provider's OAuth Client) shows a label but you cannot navigate to that record.
   It needs a "follow" arrow that opens the related record's detail page (with its
   own breadcrumb trail).
3. **Editing a related record means leaving the form.** For OIDC you should
   create/edit the OAuth client without bouncing through menus. Inline *create*
   already exists; inline *edit* and the deeper one-to-one *form embedding* do not.

Decisions taken with the user:
- **Tabs are opt-in per form** (`<Form layout="tabs">`), not automatic — existing
  forms stay as stacked sections; only forms that opt in change.
- **The 1:1 mechanism is layered**: Phase A ships inline create/edit/follow on the
  relation control (no backend change); Phase B adds true embedded one-to-one
  fields (needs backend nested write) as a follow-up.

Everything below composes existing primitives. Nothing is hand-rolled — the gaps
are extensions to the owners (`@angee/base` form/relation primitives, the SDK
route manifest), per `AGENTS.md` → "Compose, never re-implement … fix or extend it
at its owning level."

## Existing building blocks to reuse (do not re-create)

- **Tabs primitive**: `packages/base/src/ui/tabs.tsx` (`Tabs`, `Tabs.List`,
  `Tabs.Tab`, `Tabs.Panel`, `Tabs.Indicator`) — base-ui wrapper, variants
  `card`/`page`/`pill`. `FormView` already renders it for `recordTabs`.
- **Form sectioning**: `FormView.formSections()` / `FormSection` /
  `FormGrid` (`packages/base/src/views/FormView.tsx`,
  `packages/base/src/ui/form-layout.tsx`) already turn `<Group>`s into labelled
  two-column sections.
- **Declarative form DSL**: `<Form>`/`<Group>`/`<Field>` parsed by
  `parsePageGroups` (`packages/base/src/views/page/index.ts`); `<Form>` extends
  `FormViewProps`, so a new `FormView` prop flows through automatically.
- **Inline create**: `RelationPicker` (`packages/base/src/views/RelationPicker.tsx`)
  already mounts a `FormView` create dialog; `RelationFieldWidget`
  (`.../RelationFieldWidget.tsx`) auto-wires it from SDL metadata
  (`relation.model`, `relation.labelField`, `relation.canCreate`).
- **Form registration seam**: `defineAddon`'s `forms: { Model: … }`
  (`packages/sdk/src/define-addon.ts`) — one declaration reused by the page form
  AND the relation-picker dialog (`docs/frontend/guidelines.md` already documents
  this).
- **Routed detail navigation**: `RoutedRecordController`
  (`packages/base/src/views/DataPageRouted.tsx`) computes a collection base path +
  `recordPath(base, id)`; route-derived breadcrumbs come from
  `route-static-data.ts`. Reuse `recordPath`'s id-encoding rule.
- **Linked cell**: `TextLink` (per guidelines, "compose `TextLink` … never a
  bespoke link class").

---

## Feature 1 — Opt-in tabbed forms (`@angee/base`)

Render a form's existing `<Group>`s as tab panels when the form opts in. Reuse the
`Tabs` primitive and the existing `FormSection`/`FormGrid` body — only the section
*container* changes from stacked to tabbed.

**Files**
- `packages/base/src/views/FormView.tsx`: add `layout?: "stacked" | "tabs"`
  (default `"stacked"`) to `FormViewProps`. When `"tabs"`, render the
  `formSections()` groups inside `Tabs`/`Tabs.List`/`Tabs.Panel` (one tab per
  labelled group) instead of stacked `FormSection`s. The `title`/`body`/`status`
  fields and any ungrouped fields stay in the header above the tab strip (FormView
  already separates them). Empty/hidden groups (all fields `showWhen`-hidden) drop
  their tab. A group with no `label` is not a valid tab — fall back to stacked or
  throw a clear dev error. Note: the existing `recordTabs` already owns a `Tabs`
  root with `value`/`onValueChange`; the form-body tabs are a *separate* inner
  `Tabs` (the Overview body's internal layout), so the two must not collide.
- `packages/base/src/views/page/Group.tsx`: optionally add `icon?`/`badge?` to
  `GroupProps` (+ `groupDescriptor` in `page/index.ts`) so a tab can show a glyph;
  defer if not needed for the first cut.
- `packages/base/src/views/Form.tsx`: nothing — `layout` flows through the
  `Omit<FormViewProps,…>` extension automatically.

**Addon usage**
- `addons/angee/integrate/web/src/connect/views/ProvidersPage.tsx`: add
  `layout="tabs"` to its `<Form>`. The 6 groups become tabs; `displayName` (title)
  stays in the header. No new strings (group labels become tab labels).
- Optionally `addons/angee/iam/web/src/views/OidcProvidersPage.tsx` once Feature 3
  lands (its embedded OAuth-client tabs ride the same layout).

**Tests/stories**: `FormView.test.tsx` — tabbed layout renders a tab per group,
switches panels, and still drops hidden fields from the payload; add a tabbed-form
story in `@angee/base`/storybook.

---

## Feature 2 — "Follow relation" arrow + breadcrumbs (SDK manifest + `@angee/base`)

A relation control knows the *model* but not its *route*; there is no model→route
index today (`RoutedRecordController` only derives the *current* route's base path).
Build that index at composition time, then add a follow affordance.

**Model→route index (the core new piece)**
- `packages/sdk/src/define-addon.ts`: add optional `model?: string` to
  `AddonRoute` — a collection route declares the model it lists. (Inherited by the
  rendered binding's `BaseAddonRoute`.)
- `packages/base/src/createApp.tsx`: from the composed routes, build a
  `model → collection path` index (fail-fast on two routes claiming one model,
  matching the existing `claim()` discipline). Expose a `useModelRoute(model)` hook
  (returns the base path or `undefined`). This is build-time/deterministic — the
  route tree is statically composed.
- Routed-page helpers `consolePage(...)` (currently duplicated in
  `addons/angee/iam/web/src/index.ts` and `addons/angee/integrate/web/src/index.tsx`):
  thread the model so the **collection** route is tagged `model`. (These dup helpers
  are a candidate to unify into one `@angee/base` helper later — note, don't expand
  scope now.)

**The affordance**
- `packages/base/src/views/RelationFieldWidget.tsx`: it already has
  `relation.model` + the selected `value`; resolve `useModelRoute(relation.model)`
  and, when a route + value exist, render a follow arrow (a `<Glyph>` icon-button
  linking to `recordPath(base, value)`) next to the dropdown. Hidden when no route
  is registered or nothing is selected.
- `packages/base/src/widgets/many2one.tsx`: read mode (`Many2OneRead`) renders the
  label via `TextLink` to the same target when a route exists (today it is a plain
  `<span>`). Requires passing `relation` into the read render props — confirm the
  read path carries it; if not, thread it from `BoundFieldRow` in `FormView`.
- `packages/base/src/widgets/RelationField.tsx`: host the arrow inline next to the
  trigger (so it shows on the dropdown per the request), not as a separate column.

**Breadcrumbs**: no new code. Following navigates to the target's detail route,
which renders its own route-derived trail (`IAM / OIDC Providers / <id>`). A deeper
"trail of origin" (Integration → its OAuth client) is **out of scope** for v1.

**i18n**: base keys for the follow/edit aria-labels via `useBaseT()`.

**Tests/stories**: `createApp.test.ts` — index built + dupe-model fail-fast;
RelationField/widget story with a follow target.

---

## Feature 3 — Inline create / edit / follow on a relation

### Phase A — inline edit (no backend change) — ship now

Inline *create* already works; generalize the picker to also *edit* the selected
record and to *follow* it (Feature 2 affordance).

**Files**
- `packages/base/src/views/RelationPicker.tsx`: generalize the existing
  create-dialog to also EDIT. Add an `edit` affordance (pencil icon-button, enabled
  when `value` is set) that opens the same `FormView` dialog with
  `id={value}` (edit) instead of `id={null}` (create); reuse the
  `ControlBandProvider host={undefined}` wrapper and `onSaved` refetch.
- `packages/base/src/views/RelationFieldWidget.tsx`: pass `edit` through; refetch
  options after an edit save (label may change).
- `addons/angee/integrate/web/src/index.tsx`: register
  `forms: { OAuthClient: <the grouped form, layout="tabs"> }` so the page form AND
  the inline create/edit dialog share ONE declaration (DRY — per the documented
  `forms:` reuse). Refactor `ProvidersPage` to consume the registered form rather
  than re-declaring its `<Group>`s inline.

**Result for OIDC**: the OAuth Client dropdown on the OIDC provider form gains
[+ create] (exists) · [✎ edit] · [↗ follow]; you configure the OAuth client in a
tabbed dialog without leaving the page.

**Tests/stories**: RelationPicker edit path (open dialog seeded with the record,
save, option relabels); update `docs/frontend/guidelines.md` `RelationPicker` note
to mention `edit`.

### Phase B — embedded one-to-one fields (frontend + backend) — follow-up

The deeper "extend the relation's form" mechanism. Deferred per the layered
decision; outline so Phase A is built with it in mind:

- **Frontend**: a `<Field name="oauthClient" embed>` (or a dedicated `<Relation
  embed>`) that pulls the related model's registered `forms:` declaration and
  renders its groups as additional tabs on the parent form, collecting nested
  values into the submit payload.
- **Backend (nested write)**: extend the 1:1 input to accept a nested create/patch
  instead of only a `GlobalID`, or add a single mutation that writes both rows in
  one transaction — `addons/angee/iam_integrate_oidc/schema.py`
  (`OidcClientInput.oauth_client`) + `addons/angee/integrate/schema.py`
  (`OAuthClientInput`/`OAuthClientPatch`). strawberry-django owns nested write;
  follow its shape (consult `docs/stack.md`).
- Open design question to settle when Phase B starts: nested-input vs dedicated
  compound mutation, and transaction boundaries.

---

## Cross-cutting

- **i18n**: new aria-labels (follow/edit) in the base namespace; tab labels reuse
  the existing `*.group.*` keys. No hardcoded copy.
- **Glyphs**: follow/edit icons must be registered glyphs in
  `packages/base/src/chrome/icon-registry.ts` and rendered via `<Glyph>` (never a
  direct `lucide-react` import).
- **Docs**: extend `docs/frontend/guidelines.md` — `<Form layout="tabs">`, the
  `useModelRoute`/route `model` tag, and `RelationPicker` `edit`. Don't restate
  field inventories (code carries those).

## Verification

Run the stack from the repo root workspace (`angee dev` in
`.angee/workspaces/integrations-improvements`), then:

- **Feature 1**: open `/integrate/providers/$id` — the form shows tabs (Client,
  Endpoints, Behavior, Scopes, Claims, OAuth Metadata); switching tabs preserves
  entered values; Save still works and hidden fields aren't sent.
- **Feature 2**: on the OIDC provider form, the OAuth Client select shows a follow
  arrow; clicking it lands on the OAuth client's detail page with its breadcrumb
  trail. A relation whose model has no routed page shows no arrow.
- **Feature 3A**: from the OIDC provider form, create a new OAuth client inline and
  edit an existing one via the pencil — both without leaving the page; the dialog
  uses the tabbed OAuthClient form; the select relabels after edit.
- **Checks** (`docs/frontend/guidelines.md`): `pnpm run typecheck`, `pnpm run test`
  (not just `tsc` — catches icon-registry composition + stale assertions),
  `pnpm run build`. Add/extend vitest for FormView tabs, createApp model-route
  index, and RelationPicker edit. Browser-verify the three flows.

## Follow-up shipped — OIDC OAuth-client enabled state (list + detail + grouping)

The OIDC provider didn't show whether its OAuth client was enabled.

- **Backend** (`addons/angee/iam_integrate_oidc/schema.py`): `OidcClientType.oauth_enabled`
  (`select_related=["oauth_client"]`) surfaces the OAuth base's `is_enabled` flat for
  the list/detail. Plus a `rebac_aggregate_builder` with the *to-one* relation axis
  `group_by_fields=["oauth_client__is_enabled"]` + `oidc_client_groups`/`_aggregate`.
- **Frontend** (`addons/angee/iam/web/src/views/OidcProvidersPage.tsx`): an
  Enabled/Disabled `booleanBadge` pill as a list column **and** a read-only detail
  field; a `groupOptions` entry (`group.field: "oauthClient_IsEnabled"`) to fold the
  list into Enabled/Disabled buckets.
- **Framework** (`packages/base/.../ListInternals.tsx` `fieldToSnake`): restore the
  Django `__` from Strawberry's `_<Capital>` camel form so a to-one group axis
  round-trips (camel key ↔ `__` SNAKE_UPPER enum); no-op for ordinary fields. Unit
  test `views/group-dimension.test.ts`.
- **Dependency**: needs `strawberry-django-aggregates>=0.5.0` (to-one relation
  group-by axes). **TRANSITIONAL**: `[tool.uv.sources]` points at the local checkout
  while 0.5.0 publishes — drop that source and `uv lock` from the index once it lands.
- Verified: `compute_aggregation` grouped the 3 example providers by the relation
  axis (`[{is_enabled: False, count: 3}]`); SDL surface present; FE typecheck/test/build green.

## Reviewer fixes (architecture / django / react)

- **Critical (django)**: `OidcClient` used a bare `RebacManager` whose queryset lacks
  `scoped_for_aggregate`, so the grouped resolver `AttributeError`'d at query time →
  switched to `AngeeManager` (rebac-scoped *and* `AngeeQuerySet`-backed). Added an
  *executing* test (`tests/test_oidc.py::test_oidc_group_by_oauth_enabled`) — build-only
  coverage missed it.
- **High (arch)**: the 6 integrate route `model:` tags were the Django-dotted label
  (`"integrate.Integration"`) but `relation.model` is the bare GraphQL type
  (`stripTypeSuffix` → `"Integration"`); retagged to bare names so the follow arrows resolve.
- **High (arch, disputed)**: `oauth_enabled`'s `select_related` — verified safe under the
  admin-only OIDC scope (the "loaded N rows outside actor scope" pitfall bites narrower
  actors, which can't reach this admin surface); kept.
- **Medium (arch)**: `FollowRecordLink` now composes `TextLink` (real `<a href>`:
  cmd/middle-click → new tab, AT link role) instead of a button+navigate.
- **Medium (react/arch)**: added `groupOrderField` sort-path test coverage; renamed the
  shadowed `tabbed` → `tabbedSections`; dropped redundant `submitLabel` props (FormView
  derives them); documented edit as intentionally UX-only.

### Deferred (noted, not blocking)
- **OAuthClient `forms:` registration + ProvidersPage DRY refactor** — registered forms
  must use plain-English labels (the documented `forms:` boundary), so sharing the page's
  i18n'd tabbed form would drop i18n. Inline create/edit use SDL-labelled metadata fields
  (functional). Revisit if a shared declaration becomes worth the i18n trade-off.
- **Aggregate `permission_classes`** — `AggregateBuilder` accepts none, so the
  `oidcClientGroups`/`_aggregate` fields rely on `scoped_for_aggregate`'s fail-closed row
  scope (a non-admin gets empty, not `PERMISSION_DENIED`). Defense-in-depth gap on
  already-admin-visible data; would need a framework/library change to gate.
- **Relation-path field-gating in `rebac_aggregate_builder`** (django #3, latent) — the
  gated-axis guard checks only the root model, not a relation leaf. Harmless today
  (`OAuthClient.is_enabled` is whole-row gated, not field-gated); harden at the seam when
  a `read__<field>` gate on a to-one target appears.

## Out of scope / deferred

- Phase B embedded one-to-one fields + backend nested write (separate follow-up).
- "Trail of origin" breadcrumbs (following shows the target's own static trail).
- Unifying the duplicated per-addon `consolePage` helper into `@angee/base`
  (noted; not required for this work).
- Server-backed typeahead for >200 relation options (existing known limitation).
