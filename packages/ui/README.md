# @angee/ui

`@angee/ui` provides Angee's rendered primitives, runtime context, widgets, layouts, and views; it may depend only on the leaf `@angee/refine` and `@angee/metadata` layers and remains independent of the application composition root.

Install: `pnpm add @angee/ui`

`useRuntimeBrand` supplies addon identity to chrome. `DocumentTitle` composes
the active breadcrumb with that brand. `GanttView` is the presentational date
axis; the `gantt` collection kind composes the list owner's filter, grouping and
row paging alongside list, board and calendar views.
Resource record links resolve through the active app with
`useResourceRecordHref` or `useResourceRecordHrefLookup`.
ResourceView consumes [addon presets and scoped vocabulary](../app/README.md#app-vocabulary-and-shipped-views)
from runtime: fixed filters join the provider's effective base filter, and native
column visibility survives saved views. Relation columns retain their authored
field as their table ID even when metadata resolves a different display path.
`ResourceList`/`ListView` may declare `presetIds` for route-local shipped views,
`search={{ box, shortcuts }}` with text, facet, clause, toggle and group controls,
and `boardCard` with a title and up to four fields. The full search box is the
default; shortcuts default it to a collapsed badge trigger (`box: true` keeps
it full). Every control reads the shared model. The box keeps one line: the
chips that fit, then "+N", which opens the panel listing every active item.
Pinned favorites appear as toggles after declared shortcuts without
collapsing a box-only list. Below 36rem toolbar width only the badge trigger
remains, at its natural width beside the actions and pager. Addons contribute the same typed shortcuts through `resource#search`;
`sequence` interleaves them with page `page.*` extras, and `only`/`except` narrow
both. See [the declaration contract](src/views/resource/search/shortcuts.ts).
Named filters that fail resource-query parsing and shortcuts with unavailable
metadata-dependent targets throw with their ids in development; production
omits them and logs each id and reason once per mount. Malformed declarations
still fail fast in every environment.
`chrome.heading` takes `label`, `hint`, and `audience`; the shared list frame
supplies the live count. `chrome`
can also hide the view switcher, pager, or column chooser without changing the
collection query. Bare enum columns use `statusBadge` and metadata option labels.
A scalar stage column may declare `widget="statusBadge"`, `options`, and `tone`.
A descriptor row action marked `primary` stays visible; other inline actions
appear on hover, while `placement: "menu"` retains the row menu.
Toolbar Clear appears for changes beyond the collection default and restores its
query and fixed preset scope.
`ResourceList.createAction` places a typed server verb in the normal create
position, using its projected `record` and `permission` for visibility; the
default create label uses the resource vocabulary.
Console chatter renders the `record#aside` and `<model>#aside` children for the
active view after every layer's narrowing (`useContainer`). A `record#aside` tab
appears on record views; a model-level tab on that model's pages. Tabs a page
publishes follow the composed ones and take the same narrowing. Tab ids are
namespaced, and `aliases` keep their former ids working in `?chatterTab=` links.
The view switcher offers a collection's `resource#views` children for its
models: the built-in `list`, `board`, `calendar`, `gantt` and `dashboard`
kinds, and kinds addons contribute as `<model>#views` children with namespaced
ids. A contributed kind declares `label`, `icon`, `capabilities` and a `render`
component that reads the collection through `useResourceView()`; `?view=`,
presets and favourites name it by id. The calendar, Gantt and dashboard kinds
stay offered only where the page declares the data they need, and layers narrow
the offered kinds per model with `only`, `except` and `hide`. See
[containers](../../docs/frontend/guidelines.md#containers).
Dashboard widget titles link to the source collection, and table rows use the
resource record route. Widget options `fullViewRoute`, `recordRoute`, and
`recordParam` select explicit destinations when the default route is unsuitable.
Collection Gantt `lane` declares selected fields and returns a linked title,
optional secondary line, named people, and a short note after the last bar.
The shared `GanttLane` renders those parts; empty parts disappear. Use
`sidebarWidth` and `minRowHeight` to size rich labels; overlapping bars grow
both panes together. The lane heading comes from its resource vocabulary.
Plain lane-title clicks use the app router; modified clicks retain the native
link. For a linked lane, `onRowClick` handles a plain click ahead of `rowHref`
or the declared lane `href`; with only `onRowClick`, the title is a brand-styled
button.
Collection Date fields include the target calendar day and show date-only labels.
The initial collection window fits schedules and today at week granularity;
an explicit historical anchor and subsequent navigation use native scale periods.
Gantt day and range labels use its configured time zone; timed multi-day bars
read as one span from the start date and time to the end date and time.
Shared `formatDate` uses a compact, locale-aware calendar date and adds the year
outside the current year. Date and datetime list cells use `density: "list"`
with the full value on hover; record reads retain full labels.
`formatDateRange`, `formatRelativeTime`, and
`formatDuration` cover ranges, activity labels, and compact or full units.
The app runtime i18n provider sets the shared formatter language. Pure calls
use that language unless a locale is passed, with English before a runtime is
known. Zoned full timestamps retain seconds and now use that same language.

`useRuntimeViewAs`, `ViewAsBanner` and `ViewAsPicker` consume an injected
`RuntimeAuthState.viewAs` controller. Its identity and selectable people come from the
app's authorized identity read; the components issue no identity requests. The
banner is a `shell#notices` child. The controller's owner must implement the
actor transition, HTTP header, query reset, subscription shutdown and write
disabling before enabling preview in a host.

`FormView` consumes advertised root mutation arguments: updates use the loaded
edit baseline's revision and create retries reuse one key until acceptance or a
creation-key conflict. A stale revision keeps local edits until an explicit reload
and discard. Custom submit owners receive `baselineRecord` and `clientCreationKey`
and retain responsibility for their own operation arguments.

`GraphView` automatically lays out unpositioned nodes using measured sizes and
preserves explicit positions. Layout debt: `GraphEditor` still lays out from
declared sizes; `GraphView` should become the single automatic-layout owner and
report measured positions to the editor.

## Resource query migration

Resource views now use the resource's single `query` contract through
[`ResourceQuery`](../metadata/README.md). Addon pages declare canonical filters
and groups; the shared views validate them and adapt them to Refine and TanStack
Table. Custom resource adapters use the same owner for parsing, selections,
`toWhere`, `toOrderBy`, and `toGroupBy` instead of rebuilding dialect inputs.

`Facet.labelField` was removed. Keep the canonical relation field and the facet
title:

```tsx
// Before
<Facet field="channel" label="Channel" labelField="display_name" />

// After
<Facet field="channel" label="Channel" />
```

`label` still names the facet in the toolbar. Row and bucket labels come from the
resource's group axis; configure its display path in the backend resource
declaration, alongside the relation's identity axis. Changing a page title does
not change bucket identity. Facet overrides `filterField`, `filterMode`, and
`aggregateKey` were also removed; those facts belong to the resource query.

Use `{ channel: { exact: channelId } }` for a relation filter and
`{ field: "channel" }` for a relation group. A date group may add a declared
extraction, such as `{ field: "sent_at", granularity: "month" }`. Only fields,
operators, and extractions advertised by the resource are accepted. Group specs
no longer carry aggregate field/key overrides, and group URLs use `channel` or
`sent_at:month` rather than `~` triples. Nested legacy relation filters such as
`{ channel: { sqid: channelId } }` must be rewritten using the canonical operator.

There is no compatibility parser for old query state. Update authored defaults
and saved links; obsolete URL or favorite state raises `QueryParseError` and the
shared view offers a reset. For custom views, surface that error before issuing
dependent reads. Bounded local collections use `ResourceQuery.forRows` with
explicit field declarations and pass that query to `RowsListView` when the
visible columns do not describe all queryable fields.

`defaultGroup` and `defaultGroups` declare a list's starting group stack, one
grouping or an ordered stack per view kind (`null` disables grouping for that
view). The stack is part of the list's default state: it applies on first paint,
is not written to the URL until changed, and is what reset returns to. Group by
offers only granularities whose groups can be opened.

Search shortcuts and curated choices have separate jobs. `groupOptions` curates
the first group axes (an empty array omits curated axes); declared facets supply
axes when no explicit list is given. The box's clause and group editors always
use the complete `ResourceQuery` capabilities, including fields absent from
visible columns. Authored server collections use their declared query without
sampling a record page for choices. Keep new capabilities in the backend resource
declaration, and customize their presentation through the shared toolbar owners.

## Grouped boards

For a server resource, selecting a group in the card view discovers groups across
all matching records. The toolbar pages through groups; each lane shows its total
record count and has an independent record pager. Switching between grouped list
and card views preserves the same group and record scopes. No addon-specific
fetching is needed.

Explicit `laneSource` boards use the declared relation catalogue to include empty
lanes and support lane creation and drag ordering; their record window remains
the toolbar's record page. Bounded client resources and `RowsListView` group the
loaded collection through TanStack Table.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/ui/)
