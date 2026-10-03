# @angee/app

`@angee/app` is Angee's React composition root for routes, providers, addon manifests, code generation, and shared Vite/Vitest configuration; it is the top layer and composes `@angee/refine`, `@angee/metadata`, and `@angee/ui` without pushing application concerns back down.

Install: `pnpm add @angee/app`

The rendered host calls `bootApp({ target, loadSchemas, create })` to load
generated metadata before composition. `create` receives the loaded schemas and
returns the app to mount. See the [frontend guidelines](../../docs/frontend/guidelines.md).

Products declare the shell. An addon's `shell: { home, brand, perspective }`
names where `/` lands, its `{ name, mark }` identity (the mark a registered
glyph) and the selected perspective; `perspectives: { id: { root, home? } }`
declares named confinements. A perspective projects its menu root into the rail
and command palette and redirects console routes owned by other roots to home
with replacement. Unowned console routes (account, profile, preferences) remain
reachable, public layouts remain available, and the server still owns access.
Unknown perspectives or roots, and homes outside the selected root, fail
composition.

Shell facts layer along `dependsOn`, which the composed runtime fills from
`addon.toml`: among the addons declaring a shell, the one no other declarer
depends on is the product, and its own fields override its dependencies' field
by field. Unrelated products, or unrelated ancestors setting one field, fall
back to the framework default (no brand, no perspective, the first console
route) and record why in `composed.shell.diagnostics`. The deployment's
`ANGEE_UI` setting (`{ shell, perspectives }`) applies last; `perspective: null`
keeps the full console. `createApp`'s `home` and `confineTo` inputs and the
top-level `brand` field remain as deprecated overrides. Addons cannot claim the
`ui` translation namespace, which belongs to the rendered package.

Addons shape the menu with a dict keyed by node id (`menus: { … }`). A key in
the addon's own namespace (its id, or `<id>.…`) declares a node; any other key
alters a node of an addon it depends on, and the deployment's `ANGEE_UI.menus`
may alter any node last. `include: [id | { id, flatten }]` places other apps
under a node (an aggregator); a flattened app keeps its routes and trail but
shows its items as the aggregator's own. `remove: true` takes a node and its
subtree out and makes the pages only it reached unavailable: they stay
registered, redirect home, and drop out of record links and claims (a
`route.menu` anchor counts as a reference; a surviving reference keeps a page
available, and `routeHref.maybe` returns nothing for unavailable pages).
`hide: true` only leaves the rail: the page stays reachable by URL, link and the
command palette; `hide: false` shows it again. `only: [ids]` narrows the visible
children per layer, ignoring items the narrowing addon's dependents add.
`sequence`, `before` and `after` order siblings; included apps follow the
including node's own children in include order. Two unrelated addons setting
one field of a node fail composition. The legacy array form remains a list of
declarations; ids outside the addon's namespace are reported, not refused.

Menu entries are checked: unknown keys and malformed values fail composition,
and the deployment layer must be a mapping.

`createApp(...).explain` reports how the composition came out: the resolved
shell with the layer behind each field, the effective home and confinement, the
layer that set each menu node field, removed nodes and who removed them, hidden
nodes (by `hide` or a layer's `only`), unavailable routes with the reason, and
diagnostics such as shell fallbacks and out-of-namespace menu ids (warned in
development).

An app root can declare a collection/record pair with `resourcePageRoutes` for
an existing resource, using either `resource` or `recordModel`. Canonical claims
remain unique. A same-model route may declare `recordMatch` for its records;
`useResourceRecordHref` and `useResourceRecordHrefLookup` select a matching route,
then the canonical route. Explicit route names use `useRouteHref()`.
Descendant menus with `group: "platform"` appear in the confined app's Settings;
their explicit `route`/`params` targets admit those owner records and descendants,
while other records in the foreign app remain outside the confinement.

## App vocabulary and shipped views

An addon can specialize existing copy and declare named resource views:

```ts
{
  id: "desk",
  vocabulary: [{
    app: "desk",
    route: "desk.review", // omit for the entire app; children inherit
    messages: { notes: { title: "Reviews" } },
    resources: { "notes.Note": {
      label: "Review", pluralLabel: "Reviews", fields: { title: "Subject" },
    } },
    menus: { "desk.review": "Reviews" },
  }],
  resourceViews: [{
    id: "desk.open", label: "Open reviews", resource: "notes.Note",
    fixedFilter: { status: { exact: "open" } },
    filter: { starred: { exact: true } },
    groupStack: [{ field: "status" }], view: "board",
    columnVisibility: { updated_at: false },
    sort: { field: "title", dir: "asc" }, pageSize: 20,
  }],
}
```

The referenced app root, route, menu IDs, message keys and metadata fields must
exist. Duplicate vocabulary scopes fail composition. App scope precedes route
scope; nearer route ancestors win. Scoped field labels override
authored labels without altering wire metadata. A field may declare
`{ label: "Priority", tones: { HIGH: "warning" } }` in place of a string label;
the scoped map colors that field's badge, and route maps extend app maps.
Labels still come from enum option descriptions or declared widget options.
Ordinary addon i18n bundles
still reject duplicate keys; scoped overrides use `vocabulary`.

Preset IDs use the declaring addon's prefix and queries validate against emitted
resource capabilities. Set `defaultResourceView: "desk.open"` on a route, in
`resourcePageRoutes` options, or on a menu targeting that resource. Route defaults
inherit into record children; menu choices travel as the `preset` search key and
are admitted only on the target route. App creation rejects menu presets whose
resource does not match that route.
Editable filter, group, view, sort, page size and column visibility use the existing
URL state, including namespaced collection state. The fixed filter stays on the
declaration when the query is reset; toolbar Clear restores the route default.
Shipped views and user favorites appear
together in the view switcher. Saved favorites retain their selected preset and
native column visibility; legacy favorites retain the currently selected preset.

## App surface

An addon declares `surface` scopes keyed by menu root and, optionally, route,
just like vocabulary. An app may scope a route owned by another addon. Each
named slot shows only its listed ids; an empty list shows none. An omitted slot,
aside or drawer list keeps its current contributions, whether the host is
confined or not. Public and sign-in routes are never filtered. A route's
admission intersects with an inherited list when both name the same address.

```ts
surface: [{
  app: "desk",
  admit: {
    slots: {
      "form-view.record-chrome": ["iam.share"],
    },
    aside: ["comments", "activity"],
  },
  chatter: { tabs: ["comments", "activity"] },
  shell: { breadcrumb: true, commandSearch: true, asideOpen: false },
}, {
  app: "desk", route: "notes.detail", // may be owned by another addon
  admit: { slots: { "form-view.record-chrome": [] } },
  chatter: "hidden",
}],
```

`chatter: "hidden"` hides the aside. A route's tab list replaces its inherited
order within the admitted ids. Published tab ids can appear in either list;
Chatter validates them at runtime. Unscoped contributed tabs appear only on
record routes. The route policy projects model slots, record chrome, list
utilities, notices, user-menu items and drawers before their render owners read
them. `useSurfaceAdmission()` exposes the active policy;
`isSurfaceSlotAdmitted(admission, slot, id)` answers whether a particular slot
contribution is available. Unknown static ids and duplicate scopes fail
composition. See [`route-policy.ts`](src/route-policy.ts).

Resource mutation argument names and GraphQL types are projected from generated metadata at
[`resourceMutationsForSchema`](src/resource-projection.ts) into the metadata-free
provider contract. Hosts and pages do not maintain a second capability list.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/app/)
