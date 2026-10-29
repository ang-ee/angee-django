# Container authoring

The recipe for contributing to, declaring and narrowing containers from a web fragment. Contracts: `ContainerChild`, `ContainerAlteration`, `ContainerEntry` (`packages/ui/src/runtime/containers.ts:93-146`); compiler: `compileContainers` (`packages/app/src/containers.ts:44`); rules: [Containers](../../../../docs/frontend/guidelines.md#containers). This file shows shapes and adds no rule.

## 1. Find the address

An address is `node#name`. Framework containers sit at a kind node and take a model node: `form#sections` on every form, `projects.Task#sections` on Task and its MTI children. The name after `#` types the content through `ContainerKinds` (`packages/ui/src/runtime/containers.ts:16-66`); the inventory is the [Framework containers](../../../../docs/frontend/guidelines.md#framework-containers) table. A view kind is a `<model>#views` child (`ResourceViewKindContent`), a search shortcut a `resource#search` child ([resource views](../../../../docs/frontend/guidelines.md#resource-views)). No address for the shell place → bring the gap to the owner in `@angee/ui`; do not fork the page ([addresses, kinds and inheritance](../../../../docs/frontend/guidelines.md#addresses-kinds-and-inheritance)).

## 2. Declare a child in your namespace

A key `<id>.<name>` under an address declares a child; a second declaration of one id at one address fails composition. Content is the kind's type: a direct `Group`/`Tab`/`Action` element for `#sections`, a `ReactNode` for verbs and chrome, `ChatterTabContent` for `#aside`. From work (`addons/angee/work/web/src/index.tsx:100-119`):

```tsx
containers: {
  [`${TASK_MODEL}#sections`]: {
    "work.task-fields": { sequence: 40, content: taskWorkFormSection },
  },
  [`${TASK_MODEL}#actions-menu`]: {
    "work.task-triage-actions": { sequence: 40, requiredFields: TASK_ACTION_FIELDS, content: <TriageRecordActions /> },
  },
},
```

Add `permission` (a projected record permission the row must hold), `requires` (presence: a type-level ref the session must hold), `requiredFields` (fields the form selects for the child), `before`/`after` for position ([authoring](../../../../docs/frontend/guidelines.md#authoring), [presence](../../../../docs/frontend/guidelines.md#rules)).

## 3. Contribute an aside tab or drill-in

A `record#aside` child shows on saved model-backed record views; a `<model>#aside` child on that model's pages. `strip: false` plus `target` makes a drill-in. Gate on the fact the route already carries, never on a probe. From projects (`addons/angee/projects/web/src/index.tsx:75-84`):

```tsx
"record#aside": {
  "projects.tasks": {
    sequence: 20,
    content: {
      when: (context) => context.route?.recordEdges?.includes(TASK_MODEL) ?? false,
      label: "Tasks", icon: "project-task",
      render: (context) => <RecordTasksPane context={context} />,
    },
  },
},
```

Strip order, drill-in navigation, `related#groups` and `timeline#kinds` content are owned by [Context aside](../../../../docs/frontend/guidelines.md#context-aside); pane rows compose `PaneRow`/`PaneLabel`, never a local header.

## 4. Alter or narrow a dependency's child

Any key outside your namespace alters a child of an addon you depend on: `sequence`/`before`/`after` move it, `remove: true` drops it at composition, `hide` toggles it at render. `only`/`except` narrow an address per layer; `when: { app, route }` scopes the render verbs to pages (a route matches its descendants; an app matches the page's whole trail). Children are declared unconditionally; declaring or removing under `when` fails ([narrowing and `when`](../../../../docs/frontend/guidelines.md#narrowing-and-when)):

```ts
containers: {
  "form#chrome": { only: ["iam.share-record"] },
  "record#aside": [
    { only: ["timeline.rail", "related"] },
    { only: [], when: { route: "projects.my-work" } },
  ],
},
```

Narrowing another addon's container requires depending on it; the framework's containers are open. The deployment's `ANGEE_UI.containers` layers last and may `force` an `only`.

## 5. Vary per row with `impl` and `variant`

`impl: "<key>"` shows a child only on rows carrying that `ImplClassField` value; `variant: { of, impl }` stands in for another child on those rows and inherits its admission. Two variants of one child for one `impl` fail. Owners pass the row's implementations; the form resolves `#sections`, `#rail`, `#actions` and `#actions-menu` with `projection: true` ([variants and `impl`](../../../../docs/frontend/guidelines.md#variants-and-impl); reference: `addons/angee/messaging/web/src/channel-bridge-addon.tsx`).

## 6. Own a container on your page

Name an address on your own node, empty or with children; `models: true` makes it model-scoped, `unique: "key"` keys its children. Read it with `useContainer` and place the children with `containerContents` (declarations a parser reads) or `ContainerOutlet` (rendered nodes). From tags (`addons/angee/tags/web/src/index.tsx:47-52`, `views/TagsPage.tsx:15-24`):

```tsx
// index.tsx
containers: { "tags.tags#facets": {}, "tags.tags#columns": {}, "tags.tags#fields": {} },
// TagsPage.tsx
const scopeColumnEntries = useContainer("tags.tags#columns");
<List resource={TAG_MODEL}>
  <Column field="name" header={t("col.name")} />
  {containerContents(scopeColumnEntries)}
</List>
```

A new kind name is added by declaration merging into `ContainerKinds` (`addons/angee/iam/web/src/ShareAccess.tsx`). Memoize `models` and `impls` passed to `useContainer`; an undeclared address only warns in development (`packages/ui/src/runtime/containers.ts:438-441`).

## 7. Test the contribution

Assert the manifest with `containerChildren(addon, address)` (`packages/app/src/testing.tsx:167`). Render an owner with children placed straight into the runtime, no layering ([rendering and testing](../../../../docs/frontend/guidelines.md#rendering-and-testing)):

```tsx
const containers = containersFromChildren(RESOURCE_CONTAINERS, {
  "notes.Note#utilities": { "notes.capture": { content: <Button>Capture</Button> } },
});
render(<AppRuntimeProvider runtime={{ containers }}><ResourceViewUtilities value={value} /></AppRuntimeProvider>);
```

Pass a `containerScope` when a `when` rule matters; stories mount the same runtime through `RoutedRuntimeFixture`. In the live app, developer mode's composition dialog and `createApp(...).explain.containers` show each layer's narrowing and every removed child ([developer mode](../../../../docs/frontend/guidelines.md#developer-mode)).

<!--
Sources: docs/frontend/guidelines.md:1423-1857; packages/app/README.md:162-200; packages/ui/src/runtime/containers.ts:16-260,425-452;
packages/ui/src/runtime/contracts.ts:195-240; packages/ui/src/lib/container-outlet.tsx:17-26; packages/app/src/containers.ts:33-50;
packages/app/src/testing.tsx:160-170; addons/angee/{work,projects,timeline,tags}/web/src/index.tsx; addons/angee/tags/web/src/views/TagsPage.tsx;
examples/addons/example/notes/web/src/index.tsx:32-41.
-->
