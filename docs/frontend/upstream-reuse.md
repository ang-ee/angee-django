# Native frontend state and authored reads

TanStack Table supplies native pagination, sorting and selection contracts and
row models. Router or local React state holds the controlled values. Refine core
`useList` owns the resource request. `ResourceViewState` is a native-shape value:
`pagination: {pageIndex, pageSize}`, `sorting: [{id, desc}]`, and
`rowSelection: {[id]: boolean}`, together with Angee filter/group/view facts.
The state class and action reducer are removed. Table callbacks consume native
updaters; callers use native table commands.

URL pages remain one-based; native pageIndex is zero-based. The existing
`sort=field:asc|desc`, filter/group/calendar keys, clear sentinels, unrelated URL
keys and replace-history behavior remain. Favorites remain version 1 with their
existing persistence codecs. Multi-sort is disabled; embedded views keep local
state and do not mutate the surrounding route. Unknown totals retain their
existing disabled-next/last behavior.

React Hook Form owns form values, dirty/touched/error state and validation
execution. Refine core owns resource reads/writes. Same-record refreshes keep
dirty values and dirty status; successful full saves explicitly reset the clean
baseline. Identity changes remount the owning form. Partial/no-row responses,
manual slug intent, field permissions, nested errors and atomic line diffs keep
their domain contracts. Custom submissions use native Query mutations so a query
refresh cannot clear their pending status. `@refinedev/react-table` and
`@refinedev/react-hook-form` are no longer dependencies.

Authored singleton and batch reads use the same native queryOptions factory.
Imperative reads use
`authoredQueryOptions(queryClient, dataProvider, providerName, document, variables, models)`;
the infinite options factory also takes the host QueryClient first. This keeps
client defaults, custom hashing and model interests on the same native entry.
The key contains provider, printed GraphQL document and variables; a batch label
only addresses its result map. Infinite options have a separate namespace and
native InfiniteData. Consumers use `isFetching`, `isFetchingNextPage`,
`hasNextPage`, `fetchNextPage` and awaitable `refetch`. Infinite pages are at
`data.pages`; `rows` is temporarily retained as the history projection described
below. The old AuthoredQueryResult/AuthoredInfiniteQueryResult lifecycle types
and void command wrappers are removed.

Each native Query entry retains the union of every canonical model interest
registered for that operation until garbage collection. Model labels do not fork
the cache key and a later observer cannot erase earlier interests. Refine's
public auth and notification hooks process each final failure once for mounted
authored hooks, even with multiple observers or data-only consumers. Imperative
refreshes of those entries use the same policy; standalone imperative reads use
the host's native QueryCache error policy. The app's existing QueryClient and auth
policy remain the cache boundary. Published packages declare TanStack Query as a runtime peer.

Query cancellation discards late results. The pinned Hasura provider ignores
AbortSignal in custom request metadata, so physical HTTP abort is not promised;
that requires an upstream transport capability. There is no private fetch path.

The operator transport has one snapshot subscription. Pushes update each matching
Query entry with its requested sections; omitted fields in a partial update
preserve cached fields, while explicit null/empty fields replace them. HTTP
supplies the initial read and post-mutation refetch. Completions win in arrival
order, as before; no daemon revision field exists to establish stronger ordering.

Generated metadata and the supported recursive form-spec subset are validated
with Valibot schemas, and wire types are inferred from those schemas. Permitted
extension keys and optional sections remain supported; this does not broaden the
supported JSON Schema language.

## History retention gate

The generic infinite-history archive remains intentionally visible in source.
Deleting it would drop loaded history as the head moves. Moving it to another
module or flattening current pages would not solve that problem.

The current stream suppresses events once read permission is lost; bulk changes
can be muted and reconnect has no sequence checkpoint. Transcript timestamp
cursors have no database-PK tie-breaker, and timeline cursors depend on a live
anchor row. These protocols cannot establish authoritative retained history or
safe removal after deletion/revocation.

The next slice needs domain-owned stable signed tuple cursors plus bounded
current-scope revalidation (for example batches of at most 200 retained IDs),
returning complete survivors and absent IDs. Refresh costs O(H) for H retained
rows. Immediate revocation additionally needs an authorization/scope epoch or an
explicit polling policy. A prototype must prove moving-head retention, edits,
deletions, access changes, cursor ties/deleted anchors, scope switches and bounds
before the archive can be deleted and native InfiniteData becomes the sole
retained-history cache. This protocol work is not claimed complete here.
