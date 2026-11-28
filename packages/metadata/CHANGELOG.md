# @angee/metadata

## 0.2.0

### Minor Changes

- 37387b7: `useCalendarWindow(sources, window, { hidden, enabled })` reads all of a page's
  occurrence sources in one batch read, so a source set that grows or shrinks
  never remounts the calendar grid, and sources sharing a document and variables
  share one request; a source whose key is `hidden`, or that sets
  `enabled: false`, leaves the batch. `@angee/metadata` exports
  `canonicalResourceModelLabels(metadata, labels)`, the pure form of
  `useCanonicalResourceModelLabels`. Upgrade: pass an array of sources where
  `useCalendarWindow` took one (`[calendarWindowSource(source)]` for a typed
  source), keep them on one data provider (mixed `dataProviderName`s now throw),
  and drop the removed `CalendarWindowFetch` type; `CalendarWindowOptions` names
  the new options.
- 2d1043b: A resource field's metadata may carry `createOnly`: the field is a create-only
  implementation selector, which its update input carries and its model's save
  lets change only while the stored key is not registered. A record form reads it
  as the field's `createOnly` (`fieldsWithMetadataDefaults`), so a page no longer
  restates it, and a page's own `createOnly: false` cannot unlock it. `fieldChangeable`
  says whether a person may change a field on a stored record (updatable and not
  create-only); the board's lane drag asks it. A resource's roots may name
  `repairChoices`, its generated repair read `<res>_repair_choices(id, field)`.
  `@angee/refine`'s operation documents gain the `repairChoices` kind
  (`RepairChoicesDocumentMap`), which `angee-web-codegen` derives per resource
  whose roots name the read, as `repairChoicesDocuments` in each schema's
  `actions.ts`, validated against that schema; a root the schema lacks fails
  codegen by its name.
- 39bec0a: `GroupAxis` owns the order of an axis's groups. `GroupAxis.direction(order)` gives the
  direction an axis's groups go in under a list's order: the direction of the first key of the
  order the axis shares (`GroupAxis.shares`) or that is the field it buckets, so a date's months,
  quarters and weeks follow the date, else ascending. `GroupAxis.groupKey(row, identity?)` reads a
  record's group as groups compare, its label and then its identity (a caller that has read the
  identity already passes it; the label is read only where the axis has its own, else it is the
  identity), and `GroupAxis.compareGroups(left,
right, direction?)` compares two such keys in a direction, ascending unless asked: by label, then
  identity, each in that direction, a missing one after every other in both directions and two
  missing ones tied, so a comparison goes on to its next key. That is the same order of keys a
  server's groups read asks for, not the same order of every text: text compares in the list's
  natural order (`ResourceQuery.comparator`) where the server uses the database's collation, so
  "item 9" and "item 10" can come in either order there. `GroupAxis.groupBy(direction?)`
  orders its keys in that direction, ascending unless a caller asks, as facets and dashboards
  leave it, and its missing value stays last either way. `ResourceQuery.toGroupRead(group,
filter?, order?)` takes a list's order and asks for the level's groups in the direction it gives
  the axis; without an order it reads the resource's default order, as `toOrderBy` does.

  `GroupProjection.orderBy`'s `direction`, and so `GroupRead.orderBy`'s, widens from `"ASC"` to
  `"ASC" | "DESC"`. **Upgrade:** code that reads either and relied on an ascending order handles
  `"DESC"`; a caller of `toGroupRead` that passes no order gets its groups descending only where
  the resource's default order holds the axis's key descending.

- 37387b7: A grouping level can show only repeated values: `repeated` on a `GroupSpec`
  ("Repeated values only", `?group=<field>:repeated` in the URL) makes the server
  group read keep only groups of more than one record the person can read and
  leave out the records with no value. It is offered where the level's axis can
  tell its empty group apart; a view that cannot read it natively drops the flag
  and keeps the grouping, while an authored default that asks for it there is
  refused as a query error. `@angee/metadata` adds `ResourceQuery.toGroupRead`,
  the `GroupRead` type, `groupAxisId` and `groupSpecsFrom`. A page's `bulkActions`
  receives `collection.selectedGroups`, the selection split by its innermost
  groups (`SelectedGroup`, `SelectedGroupLevel`), absent on an ungrouped list and
  `null` when a selected row cannot be placed. `Button` and `SelectionBarAction`
  take a `disabledReason`: a reason disables the verb, which stays focusable, and
  is its tooltip and accessible description; `null`, `false` and an empty string
  are no reason.
- 804e3d3: A list read's order reaches the server as an ordered list, so a list ordered by two keys or
  more reads in the priority it shows. A GraphQL server rebuilds an input object in its type's own
  field order, so `order_by: { due: "desc", title: "asc" }` sorted by whichever of the two the
  resource declares first; and the read's key, the list batch hash and a grouped list's scopes
  compare an object with its keys sorted, so `due desc, title asc` and `title asc, due desc` were
  one request, and a pure swap of priority (a saved view, a link, back and forward) read nothing
  again.

  `ResourceQuery.toOrderBy` returns one single-key object per key, in priority order
  (`[{ due: "desc" }, { title: "asc" }]`), and an empty list for no order. `@angee/refine` names
  that shape `ListOrderBy` and takes it where a list's order goes: `listQueryMeta`'s `orderBy`,
  `GroupRowsRequest.orderBy` (`groupByVariables`' `rows_order_by`) and
  `AngeeListBatchScope.orderBy`, which no longer accept an object. A list read's `sorters` go the
  same way, one object per sorter in the order given, a dotted field nested, through
  `@refinedev/hasura` 7.0.1 as Angee's pnpm install policy patches it: the stock `generateSorting`
  merges them into one object. In `@angee/ui` the record pager's captured scope, the navigation
  scope a list publishes and a gantt's bar order follow a pure priority swap.

  **Upgrade:** pass `toOrderBy`'s result on as it is wherever a list's `order_by` goes. A caller
  that wrapped it in a list (`[query.toOrderBy(order)]`) unwraps it, since the server refuses a
  list inside a list; code that read its result as an object reads the list. A project that
  installs through Angee's pnpm policy (`packages/scripts/pnpm-policy.cjs`) gets the
  `@refinedev/hasura` patch; one that does not keeps the stock merge, so a read with several
  `sorters` is ordered by the server's field order, not the sorters' priority.

- 37387b7: Record forms and resource lists supply Archive / Unarchive, Move to trash /
  Restore and Delete themselves, from the resource's metadata, so a page no longer
  wires them. A page's `bulkActions` are added to these instead of replacing
  Delete, a bulk action runs on at most 25 rows a click (the rest stay selected),
  and Delete on a trashable resource is offered only for a row already in the
  trash. Archived rows leave default lists, dashboards and relation pickers unless
  a filter names the archive flag; every archivable list offers an "Archived"
  filter, and `useRelationPickerSearch` (now exported) and
  `useRelationPickerOptions` take `includeArchived` to list them. A list's row
  selection defaults on only when the list has lifecycle actions, `bulkActions`
  or a model that can be shared. A record verb marked `primary` now sits on the
  toolbar without a `placement`, on a saved record the form's band fills up to two
  buttons with its own unplaced, non-danger verbs, and an overflow (`⋮`) menu is
  always the last control of its group; `RecordActionBar` takes `fillBand`, `genericActions` and
  `contributedVerbs`, and `useTrashPrompt` an optional count. `@angee/metadata`
  exports `archiveFlagField`, `unarchivedFilter`, `resourceLifecycle` (with
  `ResourceLifecycle`), `canDeleteRecord` and `allowsRecordPermission`. Upgrade:
  replace `RecordActionBar`'s `menuPlacement="leading"` with `menuTrigger="icon"`
  (the trigger now follows the buttons) and drop `menuPlacement="trailing"`; stop
  importing `useTrashActions` and `useTrashRowActions`; drop a page's own Archive,
  Trash or Delete wiring and its `is_archived` filters; pass `selectable` to keep
  selection on a list that has none of those actions; pass `resource` (or
  `canRestore`) to `RemovedList` to keep its default Restore. `useBulkDelete` now
  takes the list's internal bulk runner in place of the clear-selection callback,
  so a page stops calling it and lets the list supply Delete.
- e3cb0a0: A list header shows a sort only for the key its rows visibly follow: the first
  key of the list's order that its groups do not share, and on a grouping column
  the direction its groups go in. A declared or default
  order may hold several keys, the later ones only breaking ties, and every one of
  them that has a column showed as sorted: a list grouped by kind and ordered by
  kind, key and language showed "Language, ascending" over rows not in language
  order, and the first press on that header sorted it descending. Now that first
  key's header shows its direction and a later key's shows "not sorted"; the header
  of a key every record of a group shares shows the direction its groups go in (the
  note on groups following their axis). A group's records share
  the value of a field the list groups by its whole value, a `Date` grouped by day
  included, and the members of a relation it groups by (`owner.name` under
  `owner`). A `DateTime` grouped by day, or a date grouped by month, still shows
  the date's sort, since one group holds many of its values. The new
  `GroupAxis.shares` in `@angee/metadata` answers which keys one group's records
  share, and a header's sort button and its `aria-sort` read that one rule: on a
  grouping column the button and its arrow show the groups' direction and the
  `aria-sort` says "none", since WAI-ARIA puts `aria-sort` on one header at a time,
  the key the rows follow.

  A press on a column that only breaks ties sorts it in its first direction
  (ascending, for a list's columns) as the only key the rows follow, the keys a
  group of the list shares, which no press drops, kept behind it, so every other key
  goes; the table's toggle would have turned one held ascending to descending. The
  press is decided from the list's live order when it happens: while
  a read is in flight the list shows the headers of its last settled read, and a
  second press there is the toggle's next step, not the first press again. Every
  other press is the table's own toggle, those keys kept behind it: on a header that shows a sort
  and on a column the order does not hold; a grouping column's press turns its groups between
  ascending and descending. On a list grouped in the browser, a grouping column's key
  orders the groups, and its press turns them: `[status asc, rank asc]` grouped by
  status becomes `[status desc, rank asc]`, and the open group comes before the
  closed one, its records still by rank.
  On a server-grouped list the same press sets the same order, and the groups read
  asks for the groups in that direction, so they turn too (the note on groups
  following their axis); it
  asks for each group's records in `status desc, rank asc`, so they keep following
  rank, and the Rank header keeps its sort.

  A press on the key the rows follow, when it is the order's last, turns it, the
  grouping key kept behind it, and the URL and a saved view keep the whole order it leaves.

  What does not change: the list's order holds every key, so every read
  asks for each key and a list sorted in the browser keeps its tie-breakers; no
  list reads or orders its rows differently until a person presses a header.

  **Changed API.** `ariaSortForColumn` is removed from
  `@angee/ui/views/resource-view-list-body`. **Upgrade:** a custom header renders
  its cells with `ListHeaderCell` from the same entry, which sets `aria-sort` by
  the rule above; no public function answers it alone. On a table of your own
  `ListHeaderCell` leaves out the keys a group shares only when that table sets
  `meta.groupAxes`; without it the header shows the table's first sort key.
  `@angee/metadata` gains `GroupAxis.shares(field)`, and each list's TanStack
  table carries the list's group axes as `meta.groupAxes`, which the headers read.

  `@angee/ui` now augments TanStack's `TableMeta` with the optional `groupAxes`.
  A program that imports `@angee/ui` and passes `meta` to a `useReactTable` of its
  own declares its keys by augmenting `TableMeta` too (an undeclared key no longer
  compiles), and must not name one of them `groupAxes`.

- 37387b7: Time-of-day fields (the GraphQL `Time` scalar, `HH:MM[:SS]` with no zone) can
  be shown and edited. `@angee/metadata` gives a `Time` field the new `time`
  widget and filter field type (`ResourceFilterFieldType` gains `"time"`), and
  its client-side filtering and sorting compare clocks as SQL does (`09:30`
  equals `09:30:00`). `@angee/ui` registers the `time` widget, which edits
  through the browser's time control and stores a cleared time as `null`;
  `formatTime` and `formatTimeInput` read a `Time` value as written, with no
  zone; the filter editor offers a `time` field type. The generated web types map
  `Time` to `string`.
