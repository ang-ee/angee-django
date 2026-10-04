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
Schema declaration checks, format validation and structural schema operations
come from [`angee.base.jsonschema`](../../../angee/base/jsonschema.py).

[`Step`](steps.py) owns `ANGEE_WORKFLOW_STEP_CLASSES` through the shared
implementation registry and supplies its input, output and config types.
[`StepContext`](context.py) carries those parsed
values, the execution actor, record access, identity and checkpoint state. Domain
code executes under the actor's permissions. It must keep external effects out
of database steps. Consumer settlements validate their own values; the runner
records retry, timeout and diagnostic facts on attempts.

[`DecisionStep`](reviews.py) asks independent decisions, linked directly through
`Decision.step_run` and `StepRun.decisions`. The step resumes when none is open.
`ctx.ask(*requests, state=...)` keeps continuation facts in the step's state.
The built-in `ask_decision` asks one configured question about the run's subject.

[`apply_proposals`](reviews.py) is the shared application path: as the run actor
it sets fields through normal validated model saves, resolves relation sets through
public identities, and invokes named public record methods. It returns the chosen
outcomes as a set. Application and continuation share the worker's body transaction;
failure rolls back record actions and retains final answers for operator retry.
No automatic replacement question is asked. Repeated delivery still requires
idempotent public methods. Decisions and alternatives apply in authored order;
a later selected alternative wins when it sets the same field.

The default continuation routes one distinct outcome directly. Several distinct
outcomes route `done`, with sorted outcomes and decision IDs in the output.
Consumers may map that set to their declared outcomes in `continue_with`.
`ctx.decision(id)` loads a retained decision from this run with normal read scope.
Run operators inherit decision read through the direct link, without answer grants.
Pruning detaches the link through its owner and retains the question and verdict.

The [`permission schema`](permissions.zed) lets starters discover and read the
workflows they may start, without editing them. Run readers see the pinned topology
and actor-readable execution summaries without requiring workflow or version read access.
Run actors can cancel and
reprocess their own runs. Monitoring other runs and reading unpublished drafts
requires workflow monitoring access, including read-only Studio inspection. Draft edits and
publication require workflow write access. Execution rows and artifacts are engine-owned;
the console exposes execution reads, monitor-readable draft authoring metadata,
and writer-only save and publish operations, alongside explicit operator actions through the
shared GraphQL resource and action owners.

[`Runner`](runner.py) owns execution and the tick. The run and step managers and
querysets in [`managers.py`](managers.py) own admission, locked row transitions,
cancellation and retention. A database step holds its run lock for its
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
have no human publisher and remain trusted. A refusal disables a trigger with a
readable reason; a child start fails its step with the same reason. Publishing
remains available to workflow authors, while executing as a principal requires
current delegation authority. Enabled triggers stored before this provenance was
recorded must be enabled again before human-published versions can run.
It also checks the actor's standing read access to the subject and each
record-reference field in the frozen admitted input schema. The run retains
one canonical `WorkflowRunEvidence` edge per source; ordinary input fields do
not create edges. Readers can inspect these references through the run resource,
with identities and relation-marked input values redacted when their current
record access is gone. Failed steps retain a reader-facing message in their
output; attempt errors keep diagnostic detail behind writer access. Evidence
edges are pruned with their run and do not protect source records from deletion.
The trigger's `enable_preview` discloses the source grants and the people and
groups with workflow monitoring access to an eligible enabler. Monitoring is the
workflow permission inherited by runs; starters without monitoring access do not
appear. The console shows this disclosure before it confirms enablement.
Grant listings identify each target by its model noun and disclose its record
label only while the reader retains access to that target.
Retention prunes a cited run only after its continuations and reprocesses are
pruned. An admitted event survives pruning of its run, and retained events
protect their trigger; the purge preview blocks deleting that trigger.
IO bodies run after their claim commits, without a transaction. Their result is
fenced by the attempt counter and deadline. IO timeouts leave a settlement window
below the worker's soft and hard limits; [`Step`](steps.py) owns those bounds.
Heartbeats retain that reserve against the current attempt's absolute lifetime.
The shared-worker tick wakes due waits, reaps
expired claims and redispatches undelivered ready rows. An uncertain external
effect requires operator acknowledgement unless the step declares
`effect_idempotent`. A recorded effect takes precedence over `Retryable`;
retries and time waits retain every attempt's effect marker for the current page.
Dispatch exhaustion waits for operator recovery, preserving the body's checkpoint.
Context operations stage readable artifact references until a successful
settlement; failed and superseded attempts retain no artifact evidence.
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

**Named gap: run budgets.** The rebuilt engine bounds individual attempts,
dispatch recovery, but has no owner for an overall run budget
or deadline. Workflows that need an aggregate time, page or resource budget still
need that engine contract; step bounds do not establish a run-wide limit.

Resource rows supply `key`, `name`, `subject_model`, `draft` and `publish`.
`subject_model` accepts a Django model label and stores the model's canonical
`_meta.label` spelling; exact model-label filters normalize that spelling too.
[`WorkflowDefinitionResource`](resources.py) delegates to `install_definition`,
which uses the same draft and publication owners as direct calls. Tests compose
the abstract sources through [`workflows.testing`](testing/__init__.py) and can
execute those same manager verbs synchronously through its drivers.
