# @angee/app

`@angee/app` is Angee's React composition root for routes, providers, addon manifests, code generation, and shared Vite/Vitest configuration; it is the top layer and composes `@angee/refine`, `@angee/metadata`, and `@angee/ui` without pushing application concerns back down.

Install: `pnpm add @angee/app`

An addon may declare `brand: { name, mark }` once, with a registered glyph as its
mark. The host owns `home` and `confineTo`: `createApp({ ..., home: "requests.all",
confineTo: "requests" })` projects that menu root into the rail and command
palette and redirects console routes owned by other roots to home with replacement.
Unowned console routes (account, profile, preferences) remain reachable. Unknown
roots and homes outside the selected root fail composition. Public layouts remain
available; the server still owns access. The project template exposes `home`,
`confine_to` and `console_chatter` answers. Addons cannot claim the `ui` translation
namespace, which belongs to the rendered package.

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
authored labels without altering wire metadata. Ordinary addon i18n bundles
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

Routes may set inherited `chatter: "hidden" | { tabs?: string[] }`. Record
routes show their admitted tabs; a non-record route shows only explicitly
contributed tabs. See [`chatter-routes.ts`](src/chatter-routes.ts).

Resource mutation argument names and GraphQL types are projected from generated metadata at
[`resourceMutationsForSchema`](src/resource-projection.ts) into the metadata-free
provider contract. Hosts and pages do not maintain a second capability list.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/app/)
