# Bridge cycles as workflow runs

This addon composes `angee.workflows` and `angee.integrate`; its manifest depends
on both. Neither foundational addon imports the other. There are no new models
or shipped resources. Consumers own their published workflow definitions and
backend stream declarations.

| Fact | Owner |
|---|---|
| Cadence and occurrence token | `Bridge.mark_sync_queued`, `sync_progress.queued_at` |
| Publication, frozen input, deduplication, actor admission | `WorkflowRunManager.start` |
| One active cycle and run pointer | `admit_bridge_cycle`, `BridgeProgressReporter` |
| Cursor, epoch, page application and quarantine | `SyncStream`, `advance_stream` |
| Lease, retry/backoff and attempt history | `StepImpl`, workflow engine |
| Conflict review | `CoverageGate` composing the native `GateStep`/Decision contract |
| Durable terminal delivery | `WorkflowDispatchKind.RUN_SETTLE`, subject settlement |
| Final bridge telemetry and cadence | `settle_bridge_run` composing Bridge terminal methods |

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
    bridge, *, workflow, occurrence_key, actor, input,
    available_at=None, using=None,
)
```

`input` is the native `JsonPresence(present=True, value=...)` envelope. `workflow`
is a lineage head or exact publication. Key dispatch loads the lineage head;
the manager pins its current publication. Duplicate delivery retains its prior
publication and exact frozen input, even after a later publication. A conflicting
identity raises. The subject is the concrete Bridge, and cycle identity exists
only in `dedup_key = "bridge-sync:{content_type_id}:{bridge.pk}:{occurrence_key}"`.
The actor must be the active Integration owner. Admission refuses ownerless
platform installs and non-default databases at the existing authorization
frontier; it never substitutes the workflow author.

The manager's `validate_new` locks the Bridge and rejects another non-terminal
run for that subject, including another workflow lineage. Admission records the
run public ID at `sync_progress.details.run`, marks syncing and pauses cadence
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
failures retry the attempt.

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
owner's `resolve()` operation. A timed recheck keeps missing dependencies and
semantic quarantine from silently accepting the cycle.

## Settlement and inspection

Terminal workflow transitions with a subject and a registered settlement handler
atomically retain a `RUN_SETTLE` dispatch.
The explicit `ANGEE_WORKFLOW_SUBJECT_SETTLERS` contribution declares the Bridge
base and `settle_bridge_run`; the workflows owner expands it to concrete content
type keys with collision rejection. Handler delivery and dispatch consumption
share one transaction. Bridge row locking compares `details.run` to the expected
run public ID and requires a busy stage. Success calls `record_sync`; failure,
retry exhaustion and direct Run-UI cancel call `record_sync_error`. Repeated or
late delivery cannot settle twice or overwrite a newer cycle. The pointer is
retained for inspection through the existing WorkflowRun UI, linked from the
shared integration sync fields. No connector execution UI is introduced.
