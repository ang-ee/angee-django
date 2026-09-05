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
dirty values. Successful saves advance the submitted defaults through RHF
`resetDefaultValues`, preserving edits made while the request was pending. Dirty
line arrays remain intact during refresh; a full-list save refuses a detected
server conflict and Discard reloads the canonical lines. Generated IDs for new
lines cannot be inferred across concurrent local edits, so those drafts use the
same explicit conflict boundary. Identity changes remount the owning form. Partial/no-row responses,
manual slug intent, field permissions, nested errors and atomic line diffs keep
their domain contracts. Custom submissions use native Query mutations so a query
refresh cannot clear their pending status. `@refinedev/react-table` and
`@refinedev/react-hook-form` are no longer dependencies.

Authored singleton and batch reads use the same native queryOptions factory.
Imperative reads use
`authoredQueryOptions(queryClient, dataProvider, providerName, document, variables, models)`;
this keeps client defaults, custom hashing and model interests on the same native entry.
The key contains provider, printed GraphQL document and variables; a batch label
only addresses its result map. Domain infinite reads compose native
`useInfiniteQuery` with `requestAuthoredData`, `sharedAuthoredMeta`,
`useAuthoredErrorPolicy` and `useAuthoredLiveInterest`. The generic
`useAuthoredInfiniteQuery` and `authoredInfiniteQueryOptions` are removed.
Messaging's `messageFeedOptions` owns its history protocol; consumers use native
`isFetching`, `isFetchingNextPage`, `hasNextPage`, `fetchNextPage`, `refetch` and
`data.pages`. `messageFeedRows` derives presentation without a separate row store.

Each native Query entry retains the union of every canonical model interest
registered for that operation until garbage collection. Model labels do not fork
the cache key and a later observer cannot erase earlier interests. Refine's
public auth and notification hooks process each final failure once for mounted
authored hooks, even with multiple observers or data-only consumers. Imperative
refreshes of those entries use the same policy; standalone imperative reads use
the host's native QueryCache error policy. The app's existing QueryClient and auth
policy remain the cache boundary. Published packages declare TanStack Query as a runtime peer.

Successful login/logout removes unobserved queries, resets observed queries and
clears mutation history through the native QueryClient. Resetting observed
queries also updates mounted identity and data consumers. Message-feed keys
include the current actor; an unknown actor disables the request. The shared
live provider revalidates interested reads on each WebSocket connection because
the change stream has no replay cursor. Model interests name the actual change
owners of selected relations. Invalidation cancels a matching pending first read
before refetching; otherwise native Query could join its older snapshot and lose
the change. Disabled and inactive entries remain invalidated without refetching.

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

## Message feed cursors

Messaging's `MessageQuerySet.feed_page` owns newest-first order by coalesced
send/create time and database PK. Django-signed cursors bind the current actor,
root scope, search and order version. Each request reapplies current REBAC scope;
the cursor does not grant access or require its original row still to exist.

Use `thread_message_feed`, `party_message_feed` or `circle_message_feed`. Pass
`older_cursor` back as `before_cursor`, or `newer_cursor` as `after_cursor`. Both
directions return newest-first messages, with `has_older`/`has_newer`; page sizes
are bounded to 200. Clients use those flags and render server order. A row moving
between requests can repeat, so row overlap alone is not an exhaustion signal.

These fields replace the development `party_timeline`/`circle_timeline` roots
and their public-message-ID anchors. Record-attached chatter retains its separate
record-gated paging contract.

## History retention

Message history lives only in native InfiniteData; the generic row archive is
removed. Each native page keeps its original lower cut. On refresh, the domain
options enumerate that complete window and revalidate every previously retained
ID through the matching `*_message_feed_revalidate` field. Complete survivors and
absent IDs form the authoritative result; missing or partial responses reject the
refresh. Moved messages retain their native-page owner. Their model-owned
`feed_order_key` supplies a complete ASCII sort key; consumers compare it without
parsing cursors, timestamps or public IDs.

Fixed windows combine an exclusive `before_cursor` with an inclusive
`through_cursor`. Advance only the upper cursor while `has_more_in_window` is
true. `has_older_than_through` describes history below the original lower cut,
including empty windows; existing `has_older`/`has_newer` retain their whole-scope
meanings. Empty retained windows still offer older paging when it is available.

Reads and revalidation batches are bounded to 200 rows/IDs. Refresh work grows
with loaded history and new arrivals; per-page batching and REBAC backend lookup
cost also matter. Retention has no maxPages cap. Native cancellation discards late
results and stops subsequent batches; older paging uses `cancelRefetch: false`.
Failed authoritative reads show an error instead of cached rows. Native GC
releases the sole cache after it becomes inactive.

Revalidation runs on invalidation and reconnect, with the existing focus/staleness
defaults. Unannounced permission changes remain cached until a refresh runs.
Immediate revocation needs a separate epoch or polling policy; stronger
cross-request consistency needs a server snapshot/revision contract. Neither is
implied by cursor stability or native Query lifecycle.
