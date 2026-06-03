# Grouped Data-List View — Odoo 18 Feature-Parity Spec

Read-only research note. Models a folded, lazily-loaded, two-level-paginated,
per-group-aggregated grouped list (à la Odoo 18) onto the Angee stack. It cites
the real Odoo semantics and the concrete Angee gaps. No code changed.

Cited roots:
- Odoo: `/Users/alexis/Work/.common/odoo/odoo-18.0`
- Angee FE: `packages/{sdk,base}/src`, BE schema: `packages/sdk/schema/contract.graphql`,
  example schema: `examples/notes-angee/src/example/notes/schema.py`,
  lib: `.venv/.../strawberry_django_aggregates/`

---

## 0. TL;DR — the architecture gap

Odoo's grouped list is **server-driven**: each group is a server bucket with its
own count + aggregates + lazily-loaded, independently-paginated record sub-list,
and the top-level pager paginates the **groups**.

Angee's current grouped list is **client-side partitioning of one flat page**:
`ListView` loads a single page of records (`useResourceList`, paginated over the
flat item list) and partitions *that page* in memory (`groupRows`,
`ListView.tsx:899`). Group counts come from a *separate* `useResourceGroupBy`
call (`ListView.tsx:182`). Groups are **always expanded**, there is **no fold
state**, **no lazy load**, **no per-group pager**, and the top-level pager
paginates **items, not groups**. Nested groups are a presentation regrouping of
the same flat page, not server sub-buckets.

To reach parity, grouping must move server-side: a grouped query returns
paginated group buckets (count + aggregates), and each expanded group fetches its
own paginated records on demand. The backend lib already paginates *groups* and
exposes per-group aggregates; what is missing everywhere is the **per-group
record sub-list** (an items field on a bucket / a per-group filtered list query)
and all the SDK/UI state for two pager levels + fold.

---

## 1. Odoo 18 — concrete grouping semantics

### 1.1 ORM `read_group` / `_read_group` (`odoo/models.py`)

- `read_group(domain, fields, groupby, offset, limit, orderby, lazy)`
  (`models.py:2801`). Returns a list of dicts, one per group. Each dict has the
  groupby field values, the aggregates, plus meta keys: `__domain` (the domain
  that selects this group's rows, `models.py:2919`), `__context`
  (`{'group_by': remaining_groupbys}` when lazy, `models.py:2920-2921`),
  `__range` (date bucket `{from, to}`, half-open), and `<field>_count` /
  `__count` (the group's row count).
- **lazy grouping** (`lazy=True`, default, `models.py:2842`): groups **only by
  the first** groupby; the rest are deferred into `__context['group_by']`. This
  is the mechanism behind nested groups — the client re-issues `read_group` per
  expanded group using that group's `__domain` + remaining `group_by`.
- `_read_group(domain, groupby, aggregates, having, offset, limit, order)`
  (`models.py:1960`) is the SQL engine: `GROUP BY` the groupby terms, `SELECT`
  the aggregate expressions, applies `limit`/`offset` to the **group rows**
  (`query.limit = limit; query.offset = offset`, `models.py:2002-2003`).
- Aggregate spec forms (`models.py:2807-2812`): `field` (default aggregator),
  `field:agg`, `name:agg(field)`; PostgreSQL aggregate funcs + `count_distinct`.
  `__count` → `COUNT(*)` (`models.py:2047`). The default aggregator of a numeric
  stored field is auto-added (`models.py:2871-2872`).
- `__fold`: only relational (m2o) groups carry a server fold flag, read from the
  comodel's `_fold_name` field in `_read_group_fill_results`
  (`models.py:2402-2406`). It is a *hint*; the client decides final fold state.
- `group_expand`: a field option (callable/method) that injects **empty groups**
  that have no rows, so columns/groups appear even when empty
  (`_read_group_fill_results`, `models.py:2339-2408`;
  `_read_group_expand_full`, `models.py:2334`). Kanban columns rely on it.
- `__range` (date/datetime granularity buckets, half-open `[from, to)`):
  `models.py:2648`, granularities `hour/day/week/month/quarter/year` + integer
  parts (`models.py:2816-2819`).

### 1.2 Web RPC `web_read_group` (`addons/web/models/models.py`)

- `web_read_group(domain, fields, groupby, limit, offset, orderby, lazy)`
  (`models.py:230`) wraps `read_group` and returns
  **`{ 'groups': [...], 'length': <total number of groups> }`** (`models.py:260`).
- `length` is the **count of GROUPS** matching the domain — the top-level pager
  paginates groups. When the page is full it counts the rest with a bare
  `_read_group(..., offset=limit)` (`models.py:251-256`).
- Per-group record counts come **inside** each group dict (`__count` /
  `<field>_count`); they are *not* limited by `DEFAULT_COUNT_LIMIT`, so a group's
  count is exact (see `group.js:120-128`).

### 1.3 Web ORM service (`addons/web/static/src/core/orm_service.js`)

- `webReadGroup(model, domain, fields, groupby, kwargs)` (`orm_service.js:236`)
  → RPC `web_read_group`; `readGroup(...)` (`orm_service.js:169`) → `read_group`.

### 1.4 Relational model — group representation & lazy load

- `DynamicGroupList` (`dynamic_group_list.js`): `groups` = array of `Group`;
  `count` = number of groups (`_setData`, `:24`); `records` getter flattens only
  the **non-folded** groups' records (`:52-57`); `recordCount` sums per-group
  counts (`:62-67`).
- `Group` (`group.js`):
  - `count` = the group's row count (`group.js:21`); `aggregates` = the group's
    aggregate values (`group.js:25`); `isFolded` reads `config.isFolded`
    (`group.js:51-53`).
  - **Lazy load on expand**: `toggle()` calls `this.list.load()` **only when
    currently folded** (`group.js:94-104`) — the records are fetched the first
    time a group opens, not before.
  - Each group owns its own `list` — a `DynamicRecordList` (leaf) or another
    `DynamicGroupList` (nested) chosen by whether remaining `groupBy` is
    non-empty (`group.js:26-33`). This is **per-group record pagination**: the
    group's list has its own `offset`/`limit`/`count`.
- `relational_model.js` group config & defaults:
  - `_loadGroupedList` (`:355`): always requests **only the first groupby**,
    `lazy:true` (`_webReadGroup`, `:695-717`, `groupby: [config.groupBy[0]]`).
  - Top-level **group** pager limit: `initialGroupsLimit` or, by default,
    `DEFAULT_GROUP_LIMIT = 80` when closed, `DEFAULT_OPEN_GROUP_LIMIT = 10` when
    `openGroupsByDefault` (`:357-362`, `:104-105`).
  - **Fold default**: `isFolded = '__fold' in groupData ? groupData.__fold :
    !config.openGroupsByDefault` (`:410-411`). So **folded by default** unless
    the view sets `openGroupsByDefault`. The "unset" m2o group is force-folded
    (`:419-422`).
  - `MAX_NUMBER_OPENED_GROUPS = 10` (`:106`): even when open-by-default, only the
    first 10 groups auto-expand; the rest fold (`:458-463`).
  - A group's records are loaded **only when not folded and count>0**
    (`:464-474`), via `_loadData(groupConfig.list)`; for nested groups this
    recurses into another `_loadGroupedList` (`relational_model.js:327-328`,
    dispatched by `groupBy.length`).
  - Per-group record list defaults: `DEFAULT_LIMIT = 80` (`:102`).
  - Aggregates requested in `_webReadGroup` (`:696-703`): every active field with
    an `aggregator` (except the groupby field) as `field:aggregator`.

### 1.5 List renderer — grouped UX (`addons/web/static/src/views/list/list_renderer.{xml,js}`)

- Top-level loop renders groups; record rows render **only for non-folded
  groups** (`list_renderer.xml:154-159`, `t-if="!group.isFolded"`).
- **Group header row** (`ListRenderer.GroupRow`, `xml:180-225`):
  - caret `fa-caret-right` (folded) / `fa-caret-down` (open), indented by group
    level (`xml:189-190`; `getGroupLevel`, `js:666-668`).
  - `<group display name> (<group.count>)` (`xml:191`).
  - **aggregate columns** aligned under the data columns: `getAggregateColumns`
    (`js:902`), `formatAggregateValue(group, column)` reads
    `group.aggregates[column.name]` (`js:651-664`). The header `<th>` spans up to
    the first aggregate column, then each aggregate column renders its value
    (`xml:184-223`).
  - **per-group pager** in the header (`xml:214-216`): shown when
    `showGroupPager(group)` ⇔ `!group.isFolded && group.list.limit <
    group.list.count` (`js:1658-1660`); props from `getGroupPagerProps`
    (`js:943-955`): `offset/limit/total=list.count`, `onUpdate` →
    `list.load({offset, limit})` (re-fetches that group's page).
- Click on header → `onGroupHeaderClicked` → `toggleGroup` → `group.toggle()`
  (`js:1682-1691`) → lazy load + flip fold.
- **Aggregate footer** (whole-list totals): `aggregates` getter (`js:561-648`)
  computes column sum/avg/min/max from the visible group aggregates (or the
  loaded records / selection) and renders them in the table foot
  (`xml:97-99`). Func chosen by column attr `sum|avg|max|min` (`js:589-593`).
- Top-level **group** pager lives in the view's control panel `Pager` (paginates
  the `DynamicGroupList`, total = number of groups), separate from each group's
  header pager.

### 1.6 Odoo defaults summary

| Behavior | Odoo default |
| --- | --- |
| Fold default | **folded** unless `openGroupsByDefault` (`relational_model.js:410-411`) |
| Top pager paginates | **groups** (`web_read_group.length`, `models.py:260`) |
| Group page limit (closed) | `DEFAULT_GROUP_LIMIT = 80` |
| Group page limit (open) | `DEFAULT_OPEN_GROUP_LIMIT = 10` |
| Auto-open cap | `MAX_NUMBER_OPENED_GROUPS = 10` |
| Per-group record limit | `DEFAULT_LIMIT = 80` |
| Lazy nested groups | first groupby only, rest in `__context`; recurse on expand |
| Per-group count | exact, inside each group dict (`__count`) |

---

## 2. UX of the grouped list (target)

- The table shows **group header rows only** initially; every group **folded**.
  Each header: caret (collapsed), group label, `(count)`, and the per-group
  aggregate values right-aligned under their columns (e.g. a `sum` column shows
  the group subtotal). Nested groups indent by depth.
- **Top-of-list pager** paginates the **set of groups** (e.g. "1–80 of 412
  groups"), with page-size control.
- **Expand a group** (click caret/header) → lazily fetch the first page of that
  group's records → rows appear under the header. A **per-group pager** appears
  in/near the header when the group has more records than its page limit; paging
  re-fetches only that group's records (other groups unaffected).
- **Chained group-by** (A then B): expanding an A-group reveals B sub-group
  headers (also folded); expanding a B-group reveals records. Each level
  paginates its own buckets/records.
- **Aggregate footer** at the table bottom shows whole-result column totals
  (sum/avg/min/max), matching column config.
- Empty groups appear only if a `group_expand`-equivalent is requested
  (otherwise groups with zero rows are omitted).

---

## 3. Mapping to the Angee stack — EXISTS vs MISSING

### 3.1 Backend GraphQL (strawberry-django-aggregates)

What the lib emits today (`builder.AggregateBuilder.build`, `types.make_grouped_type`):

- **Grouped buckets, paginated over GROUPS** — EXISTS.
  `<Model>GroupedResult { results: [<Model>Grouped!]!, page_info{offset,limit},
  total_count }` (`types.py:1079-1090`). The resolver applies `offset`/`limit`
  to the **group rows** and sets `total_count` to the number of groups
  (`builder.py:413-478`). This is exactly Odoo's group-level pagination.
  Wired in notes as `noteGroups(groupBy, pagination, filter)`
  (`schema.py:177`, `note_groups`).
- **Per-group count** — EXISTS. `<Model>Grouped.count` (`types.py:1054`).
- **Per-group aggregates** (sum/avg/min/max/stddev/variance/bool_and/bool_or,
  every/some, count_distinct) — EXISTS as nested fields on `<Model>Grouped`
  (`types.py:1056-1073`), driven by `aggregate_fields`. The notes example only
  declares `aggregate_fields=["id"]` → **count only** (`schema.py:159`), so the
  contract's `SaleGrouped` shows just `{ key, count }`
  (`contract.graphql:172-175`). Richer measures need a model with a summable
  column and `aggregate_fields` declared — capability present, **not exercised**.
- **Multi-axis group-by** — EXISTS but **FLAT**. `groupBy: [<Model>GroupBySpec!]`
  with multiple axes yields **one result row per composite key**, NOT nested
  collapsible sub-buckets: *"The grouped type is FLAT — no `subgroups`
  recursion. Multi-level group_by produces multiple result rows with composite
  keys"* (`types.py:1023-1024`). The composite key lives on `<Model>GroupKey`
  (`contract.graphql:165-170`). This does **not** match Odoo's lazy nested
  expansion; nested-group UX must either recurse axis-by-axis (issue a new
  grouped query per expanded parent, filtered to that parent's key) or be
  rebuilt from flat composite rows client-side.
- **Date granularity buckets** — EXISTS. `Granularity` enum on the spec
  (`contract.graphql:160-163`), bucket key fields like `createdAtMonth`
  (`contract.graphql:169`); `fill.py` provides temporal fill.
- **Group ordering / having** — EXISTS (`ordering.py`, `make_having_input`).
- **`group_expand` (empty groups)** — PARTIAL. Temporal fill exists (`fill.py`);
  there is no general "show empty relational groups" equivalent surfaced here.

**MISSING (the big backend gap):**

- **Per-group PAGINATED items.** There is **no field that returns the records
  inside a group**, paginated by `offset`/`limit`. `<Model>Grouped` carries only
  `key` + aggregates + count (`types.py:1052-1073`); no `results`/`items`
  records list. So "expand a group → fetch its page of rows" cannot be served by
  the grouped query at all today. Two ways to close it:
  1. Reuse the existing plural list query (`notes(filters, pagination)`,
     `contract.graphql:226-230`) with a filter that reconstructs the group's
     domain (the Odoo `__domain` analogue) — works for value/scalar/FK axes and
     for date buckets if the filter type can express the half-open range; the
     **client must build the per-group filter from the bucket key**, which the
     SDK does not do today.
  2. Or extend the aggregates lib to emit a per-bucket records connection
     (closer to Odoo, but new lib surface).
  Decision needed (see §4).
- **A per-group "domain"/filter on the bucket** (Odoo's `__domain`). The bucket
  exposes only `key`; turning a key back into a record filter (esp. date ranges,
  null/"No value" groups, nested composite keys) is non-trivial and currently
  unowned. A bucket-level `filter`/`domain` echo would make option (1) robust.

### 3.2 SDK (`packages/sdk/src`)

- `useResourceGroupBy` (`aggregates.ts:104`) — EXISTS. Offset-paginates the
  **group buckets** (`page`/`pageSize` → `pagination{offset,limit}`,
  `aggregates.ts:93-101,130-134`), returns `buckets` + `totalCount` (number of
  groups) + `count` (sum of returned bucket counts). This is the group-level
  pager source. Each bucket = `{ key, count }` (`aggregate-extract.ts:38-41`).
- `useAggregateQuery` (`aggregates.ts:40`) — EXISTS (ungrouped total).
- `useResourceList` (`resource-hooks.ts:75`) — EXISTS. Offset-paginated flat
  record list with `page/pageCount/hasNext/hasPrev/setPage`
  (`resource-hooks.ts:51-63`, `:133`). This is the per-group record-list engine
  candidate (one instance per expanded group, with a group filter).
- `assembleGroupByDocument` (`selection.ts:241`) — EXISTS but selects only
  `results { key {…} count } pageInfo total_count` (`selection.ts:261-262`).
  **MISSING**: it does not request per-group aggregate measures (sum/avg/…),
  and the extractor (`autoExtractGroupBy`, `aggregate-extract.ts:55`) only reads
  `count`. Per-group aggregates need both the document and the extractor
  extended to carry a measures map per bucket.

**MISSING (SDK):**

- A hook for **per-group record pagination**: given a group's key + the base
  filter, build the group's filter and drive a `useResourceList` for that group
  with its own page state. Today nothing converts a `bucket.key` into a record
  `filter` (the date-range / null / composite cases are unowned).
- **Fold/expand state** is not an SDK concern today; it must live in view state
  (§3.3). Lazy load = "only mount the per-group list hook when expanded".
- No representation of **two independent pagers** (group page vs per-group item
  page) — `DataViewState` has a single `page`/`pageSize` (`data-view-model.ts:47-48`).

### 3.3 ListView / DataToolbar / view state (`packages/base/src`)

- `DataViewState.groupStack` + `dataViewGroupStackParser` — EXISTS. Chained
  group-by is modeled (`data-view-model.ts:52`, `:92-96`, URL key `then`,
  `:107`), and the toolbar can add/remove levels (`DataToolbar.tsx:325-338`,
  `GroupOptionButton`). Granularity selection per date level EXISTS
  (`DataToolbar.tsx:465-488`).
- `useResourceGroupBy` for counts — EXISTS (`ListView.tsx:182-190`,
  `buildGroupCountMap`). But it is used only to label group headers, not to
  drive the list.

**MISSING / divergent (the big frontend gap):**

- **Top-level pager paginates ITEMS, not groups.** `ListView` drives one
  `useResourceList` over the flat record list (`ListView.tsx:192-198`) and the
  toolbar pager pages *that* (`DataToolbar.tsx:109-174`). Parity requires the
  top pager to page the **group buckets** (`useResourceGroupBy` page state),
  with records loaded per-expanded-group.
- **Client-side partitioning of one page.** `groupRows` (`ListView.tsx:899`)
  groups only the records already loaded for the current flat page; a group can
  only ever show the rows that happened to land on this page. No server bucket
  semantics, no per-group completeness.
- **No fold state.** Every group renders expanded; `GroupHeader`
  (`ListView.tsx:831-871`) has no caret, no toggle, no folded variant.
  `flattenListItems` always emits every group's rows (`ListView.tsx:949-962`).
- **No lazy load.** Records are already in memory; nothing fetches a group's
  rows on expand.
- **No per-group pager.** There is no second pager level anywhere in
  `ListView`/`DataToolbar`.
- **Nested groups are presentation-only** — `groupRows` recurses over the same
  loaded page (`ListView.tsx:931-935`), not server sub-buckets; counts for
  nested levels are summed client-side from flat buckets
  (`buildGroupCountMap`, `ListView.tsx:1015-1031`) which is only correct when
  the flat composite-key buckets cover the data.
- **Aggregate footer** — MISSING in `ListView` (Odoo `xml:97-99`). `GroupHeader`
  shows a bespoke "N words" client sum (`ListView.tsx:844-866`), not a generic
  per-column aggregate footer or per-group aggregate columns.
- **`group_expand`-equivalent (empty groups)** — MISSING in the UI.

---

## 4. Open questions / decisions for the architect

1. **Where do per-group items come from?** Decide between (a) reuse
   `notes(filters, pagination)` with a client-built per-group filter, or (b)
   extend the aggregates lib to emit a per-bucket records connection. (a) is
   less backend work but pushes "key → filter" reconstruction into the SDK and
   is fragile for date-range / null / composite keys; (b) matches Odoo's
   `__domain`-driven model but is new lib surface. Recommendation to weigh:
   have the **backend echo a per-bucket filter/domain** (Odoo `__domain`
   analogue) so (a) becomes deterministic instead of guessed.

2. **Nested grouping: recurse vs flat?** The lib's multi-axis group-by is FLAT
   composite rows (`types.py:1023-1024`), not Odoo's lazy nested buckets. Do we
   (a) keep one grouped query per expanded parent (filtered to the parent key —
   true lazy nesting, more round-trips), or (b) fetch all composite rows once and
   build the tree client-side (one query, but loads all leaf buckets up front and
   needs robust key→subgroup mapping)? This decides the SDK shape for nesting.

3. **Two pager levels in URL state.** Group page is naturally global, but
   per-group item pages are per-group. Encode per-group page state where? (per
   group in the URL is unbounded). Likely: group pager in URL (`page`/`pageSize`
   repurposed to groups when grouped), per-group page in transient component
   state, fold set in transient/URL. Confirm the desired URL contract.

4. **Fold default + auto-open caps.** Adopt Odoo's "folded by default, optional
   `openGroupsByDefault`, auto-open first N (`MAX_NUMBER_OPENED_GROUPS=10`)"? Or
   strictly folded-by-default per the requirement (simpler). Pick the group page
   size defaults (Odoo 80 closed / 10 open) and per-group item page size (80).

5. **Per-group aggregate measures end-to-end.** To show sum/avg subtotals on
   group headers + an aggregate footer, we must (a) declare `aggregate_fields`
   on a model with summable columns, (b) extend `assembleGroupByDocument` +
   `autoExtractGroupBy` to carry the measures map, (c) render measure columns in
   the group header + a footer in `ListView`. Notes has no summable column
   (`word_count` is a Python property, `schema.py:149-150`) — parity demo likely
   needs a `Sale`-like model (the contract already sketches `SaleAggregate`).

6. **Field-gate safety for new axes/measures.** Per `schema.py:130-156` and
   `_rebac_scoped`, aggregation runs with field enforcement relaxed; any new
   group-by axis or aggregate field must be a non-gated read field, or owner-only
   values leak through bucket keys/counts. Per-group item lists, by contrast, go
   through the normal REBAC-scoped list query — so the item rows and the bucket
   counts could diverge under row/field scoping. Confirm the intended
   consistency model (counts vs visible rows) before wiring.

7. **`group_expand` (empty groups).** Is showing empty groups in scope for the
   first slice? If yes, it needs backend support beyond temporal fill.

---

## 5. Parity feature checklist (Odoo behavior → Angee status)

| Feature | Odoo behavior | Backend | SDK | ListView |
| --- | --- | --- | --- | --- |
| Folded by default | folded unless `openGroupsByDefault` (`relational_model.js:410-411`) | n/a | MISSING | MISSING (always open) |
| Lazy load on expand | `group.toggle()`→`list.load()` only when folded (`group.js:94-104`) | needs per-group items | MISSING | MISSING |
| Top pager paginates GROUPS | `web_read_group.length`=#groups (`models.py:260`) | EXISTS (group offset/limit + total_count) | EXISTS (`useResourceGroupBy` page) | MISSING (pages items) |
| Per-group item pager | each group's `list` has own offset/limit (`group.js:26-33`, `js:943-955`) | **MISSING (no per-group items)** | MISSING | MISSING |
| Per-group count | `__count` inside group (`models.py:2851`) | EXISTS (`Grouped.count`) | EXISTS | EXISTS (label only) |
| Per-group aggregates | `group.aggregates` in header columns (`js:651-664`) | EXISTS (unused by notes) | MISSING (doc/extractor) | MISSING |
| Nested / chained group-by | lazy: 1st groupby, rest in `__context`, recurse (`relational_model.js:355-474`) | FLAT composite rows only (`types.py:1023`) | partial (groupStack) | presentation-only |
| Fold state / caret toggle | `isFolded` + caret (`xml:181-191`) | hint `__fold` (m2o) | MISSING | MISSING |
| `group_expand` (empty groups) | inject empty groups (`models.py:2339-2408`) | temporal fill only | MISSING | MISSING |
| Aggregate footer | whole-list column totals (`xml:97-99`, `js:561-648`) | EXISTS (ungrouped/grouped) | EXISTS hooks | MISSING |

Stack ownership (`docs/stack.md:32,56,58`): TanStack Table owns grouping/sort/
selection bindings; strawberry-django-aggregates owns aggregation/group-by;
nuqs owns URL view state. Per the constitution, per-group pagination + measures
belong to the aggregates lib / grouped query (backend owner), not to a client
re-derivation from a flat page.
