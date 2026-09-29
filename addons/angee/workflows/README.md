# Workflows

Workflows retain a graph document and execute its nodes as actor-scoped database
or IO steps. A `Workflow` owns its editable draft and revision; `WorkflowVersion` owns
the normalized immutable document selected when a run starts.

[`Definition`](definition.py) validates graph structure, bindings and results.
It also plans ready and skipped nodes from retained step rows without querying
the database. Named outcomes route control, lists of targets fan out, and joins
wait until all incoming sources have settled.
Schema declaration checks, format validation and structural schema operations
come from [`angee.base.jsonschema`](../../../angee/base/jsonschema.py).

[`Step`](steps.py) composes the existing implementation registry and supplies its
input, output and config types. [`StepContext`](context.py) carries those parsed
values, the execution actor, record access, identity and checkpoint state. Domain
code executes under the actor's permissions. It must keep external effects out
of database steps.

[`ReviewStep`](reviews.py) asks through the independent decisions addon and
applies settled answers in a worker as the run actor. Each answer carries its
resolver and frozen basis. Rejected application starts another review round;
other failures retain the answers for operator recovery. Decision admission
requires standing evidence access and creates no grants. The built-in `review`
uses this same contract for configured seats. Under `all`, differing actions
take an explicitly routed `disputed` branch.

The [`permission schema`](permissions.zed) lets starters discover and read the
workflows they may start, without editing them. Run actors can cancel and
reprocess their own runs. Monitoring other runs and reading unpublished drafts
requires workflow monitoring access. Execution rows and artifacts are engine-owned;
the console exposes read resources and explicit operator actions through the
shared GraphQL resource and action owners.

[`WorkflowRunManager` and `StepRunManager`](managers.py) own admission, claims,
results, cancellation and recovery. A database step holds its run lock for its
whole transaction. The domain writes, fenced result and successor planning
commit together. After commit, every ready row of an active run is sent to the job
queue, including parallel branches whose earlier message encountered a busy run.
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

Resource rows supply `key`, `name`, `subject_model`, `draft` and `publish`.
[`WorkflowDefinitionResource`](resources.py) delegates to `install_definition`,
which uses the same draft and publication owners as direct calls. Tests compose
the abstract sources through [`workflows.testing`](testing/__init__.py) and can
execute those same manager verbs synchronously through its drivers.
