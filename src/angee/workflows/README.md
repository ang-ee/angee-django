# Workflows

Workflows retain a graph document and execute its nodes as actor-scoped database
or IO steps. A `Workflow` owns its editable draft and revision; `WorkflowVersion` owns
the normalized immutable document selected when a run starts.

[`Definition`](definition.py) validates graph structure, bindings and results.
It also plans ready and skipped nodes from retained step rows without querying
the database. Named outcomes route control, lists of targets fan out, and joins
wait until all incoming sources have settled.
Publication freezes node labels and declared outcome labels with the graph.
Run and step reads expose those labels beside the stable keys; result aliases
have their own reader labels.
Draft save and publication resolve each `await_run` child under the author's
workflow read scope. A published parent stores the child's outcome-to-output-schema
contract in the node config; execution uses that frozen contract. A child outcome
outside it fails the await step. Publishing returns the version and the readable
published parent workflow keys that should be republished to adopt a changed child
contract.
Native run fallbacks include `done`, `error`, `canceled` and `interrupted`, each
allowing an empty output. Parents must route every frozen child outcome. Older
publications missing a native outcome retain that contract; author the missing
route and republish before reprocessing an affected parent run.
Schema declaration checks, format validation and structural schema operations
come from [`angee.base.validators`](../../../src/angee/base/validators.py).

[`Step`](steps.py) owns `ANGEE_WORKFLOW_STEP_CLASSES` through the shared
implementation registry and supplies its input, output and config types.
[`StepContext`](context.py) carries those parsed
values, the execution actor, record access, identity and checkpoint state. Domain
code executes under the actor's permissions. It must keep external effects out
of database steps. Consumer settlements validate their own values; the runner
records retry, timeout and diagnostic facts on attempts.

[`DecisionStep`](decision_steps.py) retains the current question in
`StepRun.decision`. `ctx.ask(request, state=...)` holds for its verdict and keeps
continuation facts in the step's state. Override `ask(ctx)` to declare the
question and `continue_with(ctx, decision, outcome)` only when typed output needs
adapting. A continuation may ask a replacement after an answer; the old question
remains a native execution record of the asking step's `created` operation,
accessible through `ctx.decision(id)` in that run. Reading another run's question
as evidence does not make it this run's question. An open question cannot be
replaced. The built-in `ask_decision` resolves the symbolic `subject` key in
configured actions to the run's subject public identity. Answering enqueues
`workflows.wake_decisions` on commit; the worker wakes the waiting step, and the
tick recovers a failed or skipped wake.

[`apply_proposals`](decision_steps.py) is the shared application path: as the run actor
it sets fields through normal validated model saves, resolves relation sets through
public identities, and invokes methods the model declares through `DecisionTargetMixin.decision_methods`.
It returns the sole selected outcome, or `done` for several distinct outcomes.
Application and continuation share the worker's body transaction;
failure rolls back record actions and retains final answers for operator retry.
Application failure does not automatically ask a replacement. Repeated delivery still requires
idempotent declared methods. Selected alternatives apply in authored order;
multiple alternatives cannot overlap the same record's field write.

The shared [party identity review](../workflows_parties/steps.py) defaults to
the run actor's identity-write authority. Its `apply_as: answerer` configuration
uses the answering person's current standing permissions for identity changes;
the Party owner rechecks permissions and the frozen identity facts under its
locks. This option leaves workflow execution and evidence reads under the run
actor and creates no grants. An unavailable answerer fails application.

The default continuation emits the decision public identity and chosen keys.
`ctx.decision(id)` loads a retained decision from this run with normal read scope.
Run operators inherit decision read through the step link, without answer grants.
Pruning releases the step link and retains the question and verdict.

`ctx.record(record, operation="read", label="")` stages one `StepRecord` for a
read, created, changed, deleted or called record. `ctx.load` authorizes and loads;
call `ctx.record` explicitly to retain evidence.
Admission inputs use the same relation with no step. `step.records` and
`WorkflowRun.objects.about(record_or_records)` provide both directions, including
readable ancestors. `ctx.note(message, tone="info")` adds a small recorded note.
The three reader-facing `StepRun.hold` states are `decision`, `run` and `error`;
timer, record-change and map scheduling remain internal wait mechanics.
A run's `hold` is its most urgent step hold: `error`, then `run`, then `decision`.
A run with a replacement holds nothing; the replacement carries the hold. The run
console's **Incidents** view lists the runs held on `error` or `run`
(`WorkflowRun.incident_expression`, filter `is_incident`).

A [`Schedule`](triggers.py) trigger (`source: schedule`) names a recurrence instead of a
model and condition: `recurrence` (an RRULE that `angee.scheduling` expands),
`recurrence_start` (its DTSTART, which supplies the parts the rule omits, so write
`BYMINUTE=0;BYSECOND=0` for a round hour) and `recurrence_zone`, the wall clock the
rule unrolls in, blank for the project `TIME_ZONE`. Its workflow declares no subject
model. Enabling refuses a schedule with no occurrence after now (a past one-off start,
an ended or never-matching rule) and one whose workflow rejects the input it would
start with. An enabled schedule keeps its next occurrence in `next_occurrence_at`;
the workflow tick reads only schedules that cursor has reached, records at most
1,000 occurrences per schedule per pass, moves the cursor in the same transaction,
then drains. The latest due occurrence becomes a `TriggerEvent` keyed by
`(trigger, occurs_at)` and starts one run with no subject and the request key
`trigger:<trigger>:<occurrence>`; earlier ones, missed while workers were down, are
recorded with `skipped_at` and never run. Disabling skips the schedule's pending
occurrences and clears its cursor, and admission refuses an occurrence older than the
current enablement. Trigger hooks of a schedule receive `record=None` and the keyword
`occurrence`. A schedule contributes no source grant: its runs act as the workflow
principal with the reach it already has. An hourly or finer rule follows the wall
clock through a DST change: the missing hour in spring lands on the next instant,
which runs once, and the repeated hour in autumn runs once.

`TriggerSource.watch_only` declares an observation that wakes `StepWatch` rows
without contributing trigger scope or permissions. Its `model_label` and
optional `fields` select saves through the same
`ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES` registry as admission sources. A step
locks and tests its actor-readable predicate, calls `ctx.watch(record)`, then
returns `ctx.wait(until=deadline, state=...)`.

`record_timeline(records: [{model, id}])` is the single authorized GraphQL read.
It returns per-record graphs, open readable decisions and all-open attention
counts, plus a deduplicated selection count. Run graph plans classify done,
current, certain, optional and not-run nodes. Guaranteed normal paths determine
certain steps; other reachable future steps fold into one may-also entry.
Stop cancels the run tree and withdraws its open decisions with `[]`, the stopper
and time, leaving domain records alone. Open questions retain their run tree's
audit regardless of age; deleting a touched record stops its runs and withdraws
their questions after deletion commits.
`stopped_at` closes an error hold and its future plan while preserving the
run's terminal failure audit; its steps cannot be retried after stopping.

The [`permission schema`](permissions.zed) lets starters discover and read the
workflows they may start, without editing them. Run readers see the pinned topology
and actor-readable execution summaries without requiring workflow or version read access.
Run actors can cancel and
reprocess their own runs, from the start or from a step. Monitoring other runs and reading unpublished drafts
requires workflow monitoring access, including read-only Studio inspection. Draft edits and
publication require workflow write access. Execution rows and record links are engine-owned;
the console exposes execution reads, monitor-readable draft authoring metadata,
and writer-only save and publish operations, alongside explicit operator actions through the
shared GraphQL resource and action owners.

Record verbs include **Run workflow**, which offers published, startable workflows
for the record's model or canonical MTI ancestor, collects required definition
inputs, and calls `start_workflow_run` with a client-generated UUID request key.
The workflow resource's `subject_model`, `can_start` and `is_published` filters
own discovery; run admission owns authorization, validation and replay.

[`Runner`](runner.py) owns the tick, dispatch and failure classification. The run
row owns its state: [`WorkflowRun`](models.py) settles, plans and writes its status
in `advance`, and its verbs `admit_subject`, `reopen`, `interrupt` and
`cancel_owned_work` admit, retry, settle after a restart and cancel a locked run. The
run and step managers and querysets in [`managers.py`](managers.py) own the run lock
(`lock_run`, `lock_owned_runs`), admission, row-set transitions and retention, and act on a locked run only
through those verbs. A database step holds its run lock for its
whole transaction. The domain writes, fenced result and successor planning
commit together. After commit, every ready row of an active run is sent to the job
queue, including parallel branches whose earlier message encountered a busy run.
Run admission stores its origin and exactly one protected cause in the same
insert: a parent step, a prior run, or a trigger event. Manual runs have no cause.
A subject model may implement [`RunSubject`](subjects.py) to own admission and
settlement of root runs about its records. The engine locks the run, then the
declared subject, and calls `admit_run(run)` after insertion and before retry
replanning. A refusal raises `ValidationError` and rolls back admission or retry.
The single terminal writer calls `settle_run(run, status)` in the same transaction
as the first terminal transition; the callback sees the persisted terminal output.
Children and repeated terminal operations do not invoke these hooks. Hooks must
perform database work only, with settlement kept to a small compare-and-set.
Deleting a subject does not prevent terminal settlement of its retained run;
there is no subject row to notify. Admission and reopening require it to exist.
Django's `workflows.E002` check rejects incomplete opt-ins.
Each `TriggerSource` declares grant targets through
`ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES`; a `record_changed` model opts in with
`RecordChangedOptIn` and declares its own grant scope.
When the actor is a workflow principal, admission also checks the pinned
version's human publisher against every delegation permission held through that
principal's enabled triggers. The source-owned `TriggerGrantTarget` stores the
permission and its resource beside each granted tuple. System-installed versions
have no human publisher and remain trusted. Publication with an actor requires a
user-backed actor; only an installation without an actor records no publisher.
A refusal disables a trigger with a readable reason; a child start fails its
step with the same reason. Executing as a principal requires current delegation
authority. Enabled triggers stored before this provenance was
recorded must be enabled again before human-published versions can run.
It also checks the actor's standing read access to the subject and each
record-reference field in the frozen admitted input schema. The run retains
one canonical `StepRecord` edge per source with no step; ordinary input fields do
not create edges. Readers can inspect these references through the run resource,
with identities and relation-marked input values redacted when their current
record access is gone. Failed steps retain a reader-facing message in their
output; attempt errors and stacktraces keep diagnostic detail behind run-operator access. Evidence
edges are pruned with their run and do not protect source records from deletion.
The trigger's `enable_preview` discloses the source grants and the people and
groups with workflow monitoring access to an eligible enabler. Monitoring is the
workflow permission inherited by runs; starters without monitoring access do not
appear. The console shows this disclosure before it confirms enablement.
Grant listings identify each target by its model noun and disclose its record
label only while the reader retains access to that target.
Retention prunes a cited run only after its continuations and reprocesses are
pruned. An admitted record event survives pruning of its run, and retained events
protect their trigger; the purge preview blocks deleting that trigger.
IO bodies run after their claim commits, without a transaction. Their result is
fenced by the attempt counter and deadline. IO timeouts leave a settlement window
below the worker's soft and hard limits; [`Step`](steps.py) owns those bounds.
Heartbeats retain that reserve against the current attempt's absolute lifetime.
The shared-worker tick wakes due waits, reaps
expired claims and redispatches undelivered ready rows. A ready row whose copy is
older than the 60 s recovery interval gets a replacement, which consumes one of
`ANGEE_WORKFLOW_MAX_DISPATCHES`; exhaustion parks the step for an operator. Every
copy keeps its queue position: Celery expiry is the stored database dispatch time
plus the whole allowance and one more recovery interval, so a saturated queue
still runs the step from its oldest copy and the claim fences the rest. Expiry
only sweeps copies a parked or settled step no longer needs; deferred publication
never extends it. The worker compares that database-clock instant with its own
clock: workers are assumed to run at most 45 s ahead of the database (one
recovery interval less the 15 s tick). Larger skew shortens every copy's life by
the excess. Message expiry is separate from a claimed attempt's execution deadline.
An uncertain external
effect requires operator acknowledgement unless the step declares
`effect_idempotent`. A recorded effect takes precedence over `Retryable`;
retries and time waits retain every attempt's effect marker for the current page.
Dispatch exhaustion waits for operator recovery, preserving the body's checkpoint.
Context operations stage readable record links until a successful
settlement; failed and superseded attempts retain no staged record links.
The first claim stores a random idempotency token. Each page uses
`token:page_index`, retaining its key across retries and time waits;
`ctx.next_page` starts the next page with a new suffix and retry allowance.
A failed run preserves unfinished sibling rows. Delivery and recovery leave
them inert until retry reopens the run and replans from all retained rows;
already-running IO siblings may record their results without planning.
Retry refuses while other unrouted failures remain and names those nodes.
Cancel also cancels open rows of a terminal run while preserving that run's
terminal status, outcome, output and error.
Delivery is at least once; step implementations must tolerate repeated execution.

`WorkflowRun.objects.reprocess(run, from_node=key)` starts the replacement at a node
instead of the entry, on the retained run's version, as
[`Definition.reprocess_plan`](definition.py) splits the retained rows. The node and
every node it reaches run again. Outside that reach a row is reused when it settled
with a routed result (succeeded, skipped, or failed through a declared error route),
and a map item only with its reused map; every other row outside the reach (one that
never settled, an unrouted failure, an item of a map planned afresh) runs again too.
The node must be a declared step of the run that ran. Copies keep their status,
outcome, input, output and notes, and carry no attempts, decision link, awaited run,
checkpoint or evidence, so a reused decision step grants the new run's actor nothing;
the replacement's `reprocess_of` names the retained run, which never changes. A run
with an open replacement refuses another reprocess, whole or from a step, so a repeated
request cannot start two. The step console's **Reprocess from this step**
(`reprocess_workflow_run_from_step`) lists the steps that run again
(`StepRun.reprocess_reruns`), under the same authority as reprocessing. When a step
outside the node's reach runs again after its attempt may already have made an
external effect, the operator must accept a possible duplicate
(`reprocess_workflow_run_from_step_accepting_duplicate`).

**Named gap: run budgets.** The rebuilt engine bounds individual attempts,
dispatch recovery, but has no owner for an overall run budget
or deadline. Workflows that need an aggregate time, page or resource budget still
need that engine contract; step bounds do not establish a run-wide limit.

Occurrence events past `ANGEE_WORKFLOW_RETENTION_DAYS` are pruned by the tick in
bounded batches once they were skipped, or evaluated and their run is pruned. Record
events stay, and a schedule's window lives on its cursor, so pruning never reopens it.

Resource rows supply `key`, `name`, `subject_model`, `draft` and `publish`.
`subject_model` accepts a Django model label and stores the model's canonical
`_meta.label` spelling; exact model-label filters normalize that spelling too.
[`WorkflowDefinitionResource`](resources.py) delegates to `install_definition`,
which uses the same draft and publication owners as direct calls. Tests compose
the abstract sources through [`workflows.testing`](testing/__init__.py) and can
execute those same manager verbs synchronously through its drivers.
