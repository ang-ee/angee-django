# @angee/refine

`@angee/refine` provides Angee's schema-independent Refine and Hasura transport, router, live-update, and typed-document glue; as a leaf package, it depends on no other Angee React package so the layers above it can reuse the transport contract without cycles.

Install: `pnpm add @angee/refine`

Resource lists pass their compiled query through `listQueryMeta`, which binds
the predicate and order to the stock Hasura provider's document override.
This package owns transport and native selections; resource capability and
query validation belong to `@angee/metadata`.

Group summaries, facets, and grouped list scopes share a TanStack query by data
provider, printed GraphQL document, and variables. Scope labels address results
within a caller only. A second consumer of the same request reads the native
cache, including while the first request is in flight.
Failed group and facet reads use the authored error policy: they notify and run
Refine's `checkError`, so an authentication failure can end the session.
After a failed `useAngeeGroupBy` refetch, its last buckets remain visible with
the new error until a successful read replaces them.

`createAngeeHasuraDataProvider` accepts executable `mutations` keyed by list
resource name. The app projects `{name, type}` argument descriptors from resource
metadata; unknown argument names fail before transport. Advertised
`idempotency_key` and `expected_revision` values travel in
`meta.gqlVariables`, separately from editable `variables`. The provider builds
the corresponding native CRUD document; an authored `meta.gqlMutation` is kept.
`ResourceSaveVariables.expected_revision` also forwards the precondition to a
generated save document. `STALE_REVISION`, `CREATION_KEY_CONFLICT` and
`VIEW_AS_READ_ONLY` survive bounded transport-error normalization.

[React documentation](https://docs.angee.ai/react/) · [Package reference](https://docs.angee.ai/react/reference/refine/)


Authored and generated-action mutations share one native execution and retention
owner, including `transient` zero-GC/reset/single-flight handling for secret
inputs and results. Both retain awaited refresh by default.
`acceptResponse` opts a covered caller into completion after authoritative values
have been adopted. `acceptQueryData` and `useSetAuthoredQueryData` adopt immediately
and return evidence for the native projection. If an earlier exact read later
publishes a stale result, the accepted value is restored; a partial projection
remains stale until its exact authoritative revalidation.
Return that evidence (or an array of actual setter results) from `acceptResponse`
to exclude the response's complete projection from its own derived reconciliation.
A void callback proves no query exclusion; unrelated concurrent acceptances are
never inferred by scanning the cache. Partial evidence keeps the query stale.
Actor-sensitive hooks compose `useAcceptQueryData`, which captures the originating Angee provider's
generation and rejects delayed writes after suspension, including recovery from
an earlier read. The plain imperative helper requires caller-owned lifecycle scope.
Use `{ complete: false }` for partial writes so derived data remains stale. A callback that adopts no data
must not be used as a speed switch. `records` and `relatedRecords` retain exact
canonical row effects, with `invalidateModels` as the broad fallback.

The Angee change provider owns local scheduling and acceptance lifetime even in
apps without websocket transport. Every session reset synchronously suspends it
and advances the lifetime before resetting native queries; repeated suspension
also invalidates old callbacks. Optional transport supplies subscriptions only.
The provider owns one bounded reconciliation burst for standard local
mutation effects, authored interests and native resource keys. The composed app
uses Refine manual live mode so native automatic resource invalidation does not
run a second path. Explicit `invalidationFilters`/`invalidationOptions` retain the
native caller-requested refresh policy. `useReconcileMutationResponse().batch`
passes an explicit per-command `reconciliation` collector to its callback. Forward
it as execution metadata: `mutate(variables, { reconciliation })` for authored
mutations, `action(id, args, { reconciliation })` for generated actions, or in
direct reconciliation options. It is never a GraphQL variable. Native mutation
metadata carries it only while pending and removes it on settlement.

Each command independently flushes its collected effects in `finally`; item
failures preserve their native error. Legacy commands await their own reads;
accepted responses retain background reconciliation. Unrelated commands and the
bounded live queue never wait for a held command's transport. Authentication
suspension invalidates collectors and settles waiting completions. Form-owned
exact `queryKeys` completion fallbacks join their explicit collector once with
declared effects; complete response acceptance excludes only that command's
fallback, while independent live changes still reconcile normally.
Opaque custom reloads and explicit record-action `refresh: true` remain imperative.
Foreign live providers retain native direct refresh behavior.

A subscription's `ready` result acknowledges model-group registration. The
provider handles each operation's ready, including one sent after reconnect or
resume. The reconciler snapshots matching reads at the ack and excludes an
idle read that has fetched since; accepted mutation data does not count as a
fetch. See the [readiness rule](../../docs/frontend/guidelines.md#rules) for the
race contract. No read is cancelled by reconciliation:
commands, live changes and ready let in-flight reads settle, then refetch once
for coalesced effects. Idle retained reads refetch immediately for explicit
batches and the exported invalidation helper; provider-scheduled commands
without a batch flush within the 300 ms quiet gap. Actor switches reset queries
in `create-app.tsx`; explicit keyset and dashboard store restarts, and
user-triggered refetches retain TanStack's cancelling defaults because they
replace the actor's or user's read, rather than reconcile a change. This
transport has no replay cursor or total row ordering guarantee, and occurrence
IDs are not row versions. Later
events may revalidate an accepted response because an echo cannot be
distinguished safely from a newer remote write.

Keyset feed state remains native `InfiniteData`. `acceptResponse` replaces full
returned rows in retained pages; `insert: true` requires domain proof of current
head membership, and `absent_ids` requires authoritative absence. The head must
already be loaded; acceptance never invents counts or cursors. `revalidate.models`
opts canonical retained-row updates into bounded authorized revalidation. Unknown
IDs/models, creates/deletes, absent rows, aggregate metadata and recovery use the
existing complete fixed-window refresh. Explicit refresh/restart stays available.
