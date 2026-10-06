# @angee/app

`@angee/app` is Angee's React composition root for routes, providers, addon manifests, code generation, and shared Vite/Vitest configuration; it is the top layer and composes `@angee/refine`, `@angee/metadata`, and `@angee/ui` without pushing application concerns back down.

Install: `pnpm add @angee/app`

The rendered host calls `bootApp({ target, loadSchemas, create, errorReporting })`
to load generated metadata before composition. `create` receives the loaded
schemas and returns the app to mount. `errorReporting` carries the host's Sentry
DSN and environment; without a DSN no reporting SDK is loaded. See the
[frontend guidelines](../../docs/frontend/guidelines.md).

Products declare the shell. An addon's `shell: { home, brand, perspective }`
names the product's home (where `/` lands unless the person chose otherwise,
per the [frontend guidelines](../../docs/frontend/guidelines.md)), its
`{ name, mark }` identity (the mark a registered glyph) and the selected perspective; `perspectives: { id: { root, home? } }`
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
back to the framework default (no brand, no perspective, no declared home) and
record why in `composed.shell.diagnostics`. The deployment's
`ANGEE_UI` setting (`{ shell, perspectives }`) applies last; `perspective: null`
keeps the full console. `createApp`'s `home` and `confineTo` inputs and the
top-level `brand` field remain as deprecated overrides. Addons cannot claim the
`ui` translation namespace, which belongs to the rendered package.

Addons shape the menu with a dict keyed by node id (`menus: { … }`). A key in
the addon's own namespace (its id, or `<id>.…`) declares a node; any other key
alters a node of an addon it depends on, and the deployment's `ANGEE_UI.menus`
may alter any node last. `include: [id | { id, flatten }]` places other apps
under a node (an aggregator); a flattened app keeps its routes and trail but
shows its items as the aggregator's own. `include` absorbs: the app leaves its
place on the rail. `mount` borrows: a node `{ parent, mount: "<route>", path }`
gets an alias of that route under its app, named after the node (`<id>`, and
`<id>.record` for the route's record child), while the source app keeps its
page. The alias reuses the route's page, detail page and model, so nothing is
exported or re-declared; its path is the app's `path` (`/<app id>` by default,
since an app's own target may itself be a mount) joined with the node's. A
node targets one of `route`, `mount` and `to`; an addon mounts only routes of
addons it depends on. The node's `defaultResourceView` and `recordMatch` go onto
the alias, and inside it `useRouteHref()` builds the mounted route's names as
the alias's, so the page's own links stay in the borrowing app. `remove: true`
takes a node and its subtree out and makes the pages only it reached
unavailable: they stay
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

**Developer mode** shows how the app was composed, like Odoo's debug mode. Any
signed-in user turns it on from the user menu, which stores it in their
preferences, or for the browser session with `?debug=1` on any URL (`?debug=0`
turns it off). The session choice, from the URL or the menu, wins over the
stored preference until the tab closes. A debug button then sits beside the
avatar: hovering it gives the page's route, app, perspective and home, and
clicking it opens the composition (shell provenance, removed and hidden menu
items, unavailable routes, findings, the layers behind each menu item, container
narrowing, removed container children, and the layers behind each child). The
expanded rail lists hidden items, marked "(hidden)", and removed items, struck
through under the item they showed in, each with its id and layers on hover;
form field labels and list column headers show their technical field name. It
reveals composition facts only, never records, and changes nothing the server
allows.

`createApp(...).explain` reports how the composition came out: the resolved
shell with the layer behind each field, the effective home and confinement, the
layer that set each menu node field, removed nodes and who removed them, hidden
nodes (by `hide` or a layer's `only`), unavailable routes with the reason,
diagnostics such as shell fallbacks and out-of-namespace menu ids (warned in
development), and under `containers` each layer's narrowing per address, the
removed children with who removed them, and the layer behind each child field.

An app root can declare a collection/record pair with `resourcePageRoutes` for
an existing resource, using either `resource` or `recordModel`. Canonical claims
remain unique. A same-model route (or a mount) may declare `recordMatch` for its
records; `useResourceRecordHref` and `useResourceRecordHrefLookup` open a record
at the match its row satisfies, from every app, then at the active app's own
claim, then at the canonical route. Two matches on one resource and condition
fail composition. Explicit route names use `useRouteHref()`.
In a confined app, explicit Settings `route`/`params` targets admit those owner
records and descendants, while other records in the foreign app remain outside
the confinement. See the [frontend guideline](../../docs/frontend/guidelines.md)
for the shared Settings place.

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

The referenced app (a root or an included app, flattened ones too), route, menu
IDs, message keys and metadata fields or filterable predicates must exist.
Duplicate vocabulary scopes fail composition. A page composes the scopes of
every app on its trail, outermost first, then route scopes; nearer route
ancestors win. Scoped field labels override
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

## Containers

Addons extend each other's pages through containers: named lists on a node,
addressed `node#name` (`form#sections`, `projects.Task#actions`,
`record#aside`, `shell#user-menu`), whose entries are children. The `containers`
dict is keyed by address and layered along `dependsOn` like menus. A key in the
addon's own namespace declares a child; any other key alters a child of an
addon it depends on (`sequence`, `before`, `after`, `remove: true`, `hide`); the
framework's own children are open to every addon. `only`, `except` and `when`
narrow what renders:

```ts
containers: {
  "projects.Task#sections": {
    "desk.review": { sequence: 40, content: reviewSection },
  },
  "form#chrome": { only: ["iam.share-record"] },
  "record#aside": [
    { "chatter.comments": { remove: true } },
    { only: [], when: { route: "notes.detail" } }, // may be owned by another addon
  ],
},
```

A child on a kind address (`form#sections`) shows on every model; one on a
model address also shows on that model's MTI children. The name after `#` types
the child through `ContainerKinds`. `when: { app, route, perspective }` applies
`only`, `except` and `hide` on matching pages; children are declared and moved
unconditionally. Each layer's `only` intersects with what it inherits and never
filters children its dependents add; `only: []` keeps none, and `hide: false`
undoes a `hide`, never an `only`. A child may carry `permission`,
`requiredFields`, `impl` (shown only on rows of that implementation) or
`variant: { of, impl }` (stands in for `of` on those rows). An addon declares a
container of its own on its own node, model-scoped with `models: true` or one
child per key with `unique: "key"`. The deployment's `ANGEE_UI.containers`
alters and narrows last. Unknown addresses or children, duplicate ids and
foreign-namespace declarations fail composition. The framework's containers
are `CORE_CONTAINERS` in [`core-containers.ts`](src/core-containers.ts); the
login page's are `LOGIN_CONTAINERS`. See the
[frontend guidelines](../../docs/frontend/guidelines.md#containers) and
[`containers.ts`](src/containers.ts).

Saved dashboards persist through the `dashboardStore` manifest key; at most one
installed addon provides it.

Resource mutation argument names and GraphQL types are projected from generated metadata at
[`resourceMutationsForSchema`](src/resource-projection.ts) into the metadata-free
provider contract. Hosts and pages do not maintain a second capability list.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/app/)
