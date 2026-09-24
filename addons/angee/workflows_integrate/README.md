# Archive imports and bridge cycles as workflow runs

This addon composes `angee.workflows`, `angee.integrate` and `angee.storage`.
The foundational addons do not import workflows. There are no new models
or shipped resources. Consumers own their published workflow definitions and
backend stream declarations.

| Fact | Owner |
|---|---|
| Bounded archive inspection and safe subtree staging | `archives.py` |
| Extractor registry, archive probe, mapping review and execution | `archive_steps.py` |
| Vendor recognition and target-domain ingest | Backup/takeout extractors in messaging bridge addons |
| Cadence and occurrence token | `Bridge.mark_sync_queued`, `sync_progress.queued_at` |
| Publication, frozen input, deduplication, actor admission | `WorkflowRunManager.start` |
| One active cycle | `admit_bridge_cycle` |
| Current execution owner | `Bridge.sync_run_id`, `Bridge.claim_dispatch` |
| Cursor, epoch, page application and quarantine | `SyncStream`, `advance_stream` |
| Lease, retry/backoff and attempt history | `StepImpl`, workflow engine |
| Conflict review | `CoverageGate` composing the native `GateStep`/Decision contract |
| Durable terminal delivery | `WorkflowDispatchKind.RUN_SETTLE`, subject settlement |
| Final bridge telemetry and cadence | `settle_bridge_run` composing `Bridge.settle_dispatch` |

## Archive imports

Backup/takeout extractors in messaging bridge addons contribute classes through
`ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES`. They import `ArchiveExtractor` and
`ArchiveExecutionReporter` from the public `angee.workflows_integrate.steps` path,
which re-exports the canonical classes owned by `archive_steps.py`, and compose
the shared `archives` utilities for bounded reads, safe ZIP member names and
temporary subtree staging. Extractors own vendor parsing and call their target
domain's idempotent ingest owner; the workflow addon owns orchestration.

The registered `archive_probe` operation inspects a `storage.File` or
`storage.Drive` subject with matching extractors and emits stable proposals.
Bind its output into `archive_gate`, which composes the native `GateStep` and
Decision action owner to review fixed extractor-to-target rows. Optional gate
config selects `action`, `assignee` and `max_attempts`; by default the admitted
run actor reviews the mapping, with no requester exclusion. All proposals must
share one target resource; mixed targets take the gate's `failed` outcome.

`archive_execute` with `mode: prepare` validates the retained predecessor
Decision through `DecisionManager.locked_resolution`, checks the frozen rows,
and emits a plain mapping list. The native `map` operation consumes that list
and invokes `archive_execute` with `mode: unit` for each extractor. Blob and
import I/O runs outside the workflow finalization transaction. The native lease
keepalive covers recognition, staging and execution; extractors can also pulse
the reporter's heartbeat during ingest, and native Map results retain partial
failures. Repeated imports converge through the target domain's ingest identity.

Archive execution currently requires the default database because Decision
authorization and external backup ingest owners do not yet carry a complete
database-alias contract. Entry checks reject other aliases before reading an
archive or calling an importer. Framework relation reads and reporter heartbeats
use the selected write alias; the reporter exposes it as `using`.

## Admission

A Bridge subclass declares `sync_workflow_key: ClassVar[str] = "consumer-cycle"`.
The normal queue token remains the cadence occurrence. `Bridge.sync()` composes
its `dispatch_sync()` hook, contributed through `ANGEE_BRIDGE_SYNC_DISPATCH`,
and returns `SyncDispatch.DISPATCHED`. Direct bridges keep returning an integer.
Override `sync_workflow_input(*, using=None)` to add immutable admitted facts to
the default `{ "bridge": { "model": "app.model", "id": "public-id" } }` input.
Overrides receive a Bridge pinned to the selected write database. A connector
that overrides `sync()` must compose `super().sync()` to select this behavior.

The public admission function is:

```python
admit_bridge_cycle(
    bridge, *, workflow, occurrence_key, actor, input=None,
    prepare=None, available_at=None, using=None,
)
```

Explicit `input` is the native `JsonPresence(present=True, value=...)` envelope;
omission snapshots the locked Bridge through `sync_workflow_input`. The optional
database-only `prepare(using)` runs after native workflow/retained-run locks,
before Bridge is locked and input is constructed. Consumers acquire upstream
scope locks in preparation and downstream scope locks during input construction.
`dispatch_bridge_cycle` forwards the same optional preparation hook. `workflow`
is a lineage head or exact publication. Key dispatch loads the lineage head;
the manager pins its current publication. Duplicate delivery retains its prior
publication and exact frozen input, even after a later publication. A conflicting
identity raises. The subject is the concrete Bridge, and cycle identity exists
only in `dedup_key = "bridge-sync:{content_type_id}:{bridge.pk}:{occurrence_key}"`.
The actor must be the active Integration owner. Admission refuses ownerless
platform installs and non-default databases at the existing authorization
frontier; it never substitutes the workflow author.

The manager's `validate_new` runs under the Bridge lock and rejects another non-terminal
run for that subject, including another workflow lineage. Admission claims the
run through `Bridge.claim_dispatch`, marks syncing and pauses cadence
until terminal settlement. Duplicate admission never rewrites a newer pointer.

## Bounded execution

Register concrete consumer subclasses through `ANGEE_WORKFLOW_STEP_CLASSES` or
select the shipped `integrate_stream` operation. `BoundedStreamStage` accepts:

```json
{
  "bridge": {"model": "app.bridge", "id": "public-id"},
  "key": "contacts",
  "partition": "address-book",
  "page_bound": 100
}
```

The reference must match `WorkflowRun.subject`. `open_stream` composes backend
declarations; `begin_stream_cycle` prepares discrepancy rescan; `advance_stream`
owns the one-page operation. A stage is `STANDARD`, `WRITE`, idempotent,
`FRESH`-replayable and non-deterministic. No database transaction spans extraction
or other remote I/O. Mechanism A commits local applications, discrepancies and
cursor together; workflow finalization follows separately. A crash between
those commits resumes from the durable cursor. `heartbeat_during` renews the
exact retained attempt lease during long pages. Declare automatic retry/backoff
in native step config; semantic records remain quarantined while infrastructure
failures retry the attempt. Adapter contract violations fail immediately without
retaining a retry.
Consumer stages resolve the admitted Bridge with the public `bridge_for_step`.

Each invocation handles one page. Incomplete pages return a timer wait due now,
with stream public ID, generation and accumulated `cycle_items` in native
`StepRun.resume_state`. Completion publishes that total as `counts.cycle_items`
alongside the final `counts.page_items`, discrepancy IDs and record evidence.
Settlement sums the cycle totals from successful stream stages. Finalized pulse
counts survive waits and retries, including an empty final page. A crash between
the driver's page commit and workflow finalization can still omit that pulse's
count; data replay follows the committed cursor without applying the page twice.

There is **no per-record Map**. The workflow DAG coordinates independent stream
stages; a single logical stream partition must have one writer stage at a time.
`integrate_coverage` accepts `bridge` and `streams: [{key, partition}]`, waits
while any required discrepancy is OPEN/RETRY, and uses native GateStep slots for
one Decision per open CONFLICT. A Decision asks for review and rechecking; it
does not resolve a data conflict. The domain resolution must call the discrepancy
owner's `resolve_conflict(keep=...)` operation. A timed recheck keeps missing
dependencies and semantic quarantine from silently accepting the cycle.
Coverage uses STANDARD execution so waiting pulses can perform remote reads.
Each pulse re-drives due discrepancies for one partition, bounded by
`rescan_bound` (default 100), and rotates through admitted streams. Adapters
without identity reads fall back to a bounded baseline that must finish before
acceptance. Conflicts still require explicit resolution, and another active cycle
remains barred by admission.

## Settlement and inspection

Terminal workflow transitions with a subject and a registered settlement handler
atomically retain a `RUN_SETTLE` dispatch.
The explicit `ANGEE_WORKFLOW_SUBJECT_SETTLERS` contribution declares the Bridge
base and `settle_bridge_run`; the workflows owner expands it to concrete content
type keys with collision rejection. Handler delivery and dispatch consumption
share one transaction. `Bridge.settle_dispatch` compares `sync_run_id` to the
expected run ID under the Bridge row lock and requires a busy stage. Success calls
`record_sync`; failure, retry exhaustion and direct Run-UI cancel call
`record_sync_error`. Repeated or late delivery cannot settle twice or overwrite a
newer cycle. The pointer is
retained for inspection through the existing WorkflowRun UI, linked from the
integration sync card through this addon's web fragment. Progress details can be
replaced freely without changing execution ownership.
