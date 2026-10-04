# Integration workflows

## Bridge sync cycles

[`SyncCycleBridge`](sync.py) composes workflow admission and settlement with
integrate's existing scheduling and dispatch lifecycle. A consumer composes this
mixin before `Bridge` and its Integration parent, declares `sync_workflow_key`,
and implements the database-only `lock_sync_scope()` and `sync_workflow_input()`
hooks. The workflow declares that concrete bridge as its subject model. Scope
locks protect the snapshot; do not acquire the bridge lock before the engine's
run lock. Input is retained once per queue occurrence and replay uses the retained
input without resnapshotting.

Connecting grants the Integration owner `starter` through the workflow's record
grant owner. Runs always use that active owner, and normal start authorization
still applies on replay. Admission refuses another active root cycle. The
engine's `RunSubject` hook settles successful, failed, timed-out and canceled
cycles through `Bridge.settle_dispatch`; retries reclaim the settled dispatch.
Success sums completed stream-stage outputs, including mapped partitions.

[`StreamStage`](steps.py) (`integrate_stream`) applies one bounded page per IO
attempt, checkpoints acknowledged counts through `next_page`, and resumes from
the cursor that integrate committed with the page. Fan out partitions with the
native workflow `map`. The shared `PageResult` continuation guard rejects stalled
pages and repeated baseline resets; checkpoints retain its digest and reset count,
never a second cursor. No bridge-reference input or separate dispatch setting is
needed: steps resolve `ctx.subject` through the engine's declared subject owner.

Coverage uses two steps with the same explicit partition set. `integrate_rescan`
performs bounded rescan/baseline recovery per partition and waits on a deadline
while discrepancies remain. `integrate_conflicts` asks one decision per conflict;
its continuation checks current discrepancy truth. Unresolved discrepancies fail
the step; after a direct domain resolution, operator retry consumes the retained
answers without asking again. Resolving a conflict belongs to the discrepancy owner; a
review answer only asks to recheck it. Put conflict review before the rescan wait
or on a parallel graph branch so an unresolved conflict can reach review.

Counts describe acknowledged page results. A page committed just before a worker
loses its lease is not replayed, but its unacknowledged count is absent from the
workflow total. Exact totals across that boundary need a durable counter at the
stream/page owner; event-feed totals cannot be reconstructed from replica links.
Run-wide budgets remain the named gap in the [workflows README](../workflows/README.md).

## Archive imports

`archive_import_file` and `archive_import_drive` share one graph. Registered
`ArchiveExtractor` implementations recognize a readable storage subject,
the gate confirms target mappings supplied in its node configuration, and the
workflow `map` runs one IO attempt per confirmed mapping. Extractors own their
vendor parsing and idempotent target ingest. Each attempt reports progress
through `ArchiveExecutionReporter.heartbeat()`; the workflow retains source
and target artifacts. Missing or incompatible mappings take `unsupported`; the
card offers fixed import or skip alternatives, with no target-input answer form.

`ArchiveExtractor` owns the `ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES` registry;
its addon declares that base through `ANGEE_IMPL_REGISTRIES`.
Implementations import `ArchiveExtractor` and `ArchiveExecutionReporter` from
`angee.workflows_integrate.archive_steps`. Safe ZIP helpers live in
`angee.storage.archives`. The framework's workflow and decision owners handle
review settlement, map fan-out, retries, and run results.
