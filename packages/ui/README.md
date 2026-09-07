# @angee/ui

`@angee/ui` provides Angee's rendered primitives, runtime context, widgets, layouts, and views; it may depend only on the leaf `@angee/refine` and `@angee/metadata` layers and remains independent of the application composition root.

Install: `pnpm add @angee/ui`

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

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/ui/)
