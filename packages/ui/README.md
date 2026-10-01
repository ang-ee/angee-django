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
`filterRow` with filter-option or shipped-preset quick ids and facet ids, and
`boardCard` with a title and up to four fields. Saved favourites can be renamed
and pinned into the filter row. Quick ids and pinned favourites appear as chips;
the Favorites menu still owns saving and pinning. `chrome.heading` takes `label`,
`hint`, and `audience`; the shared list frame supplies the live count. `chrome`
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
Console chatter follows the app's inherited surface policy. A named slot,
aside or drawer list restricts that address; an omitted address keeps its
contributions. Public and sign-in routes stay unfiltered. An unscoped chatter
contribution appears on record routes by default; a route that lists it by id shows it on any page.
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
banner fits `CONSOLE_NOTICE_SLOT`. The controller's owner must implement the
actor transition, HTTP header, query reset, subscription shutdown and write
disabling before enabling preview in a host.

`FormView` consumes advertised root mutation arguments: updates use the loaded
edit baseline's revision and create retries reuse one key until acceptance or a
creation-key conflict. A stale revision keeps local edits until an explicit reload
and discard. Custom submit owners receive `baselineRecord` and `clientCreationKey`
and retain responsibility for their own operation arguments.

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

Toolbar shortcuts and custom choices have separate jobs. `groupOptions` curates
the quick groups (an empty array means no shortcuts); declared facets supply
shortcuts when no explicit list is given. Custom filter and group editors always
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
