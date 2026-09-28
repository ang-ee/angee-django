# Workflows

Workflows retain a graph document and execute its nodes as actor-scoped database
steps. A `Workflow` owns its editable draft and revision; `WorkflowVersion` owns
the normalized immutable document selected when a run starts.

[`Definition`](definition.py) validates graph structure, bindings and results.
It also plans ready and skipped nodes from retained step rows without querying
the database. Named outcomes route control, lists of targets fan out, and joins
wait until all incoming sources have settled.

[`Step`](steps.py) composes the existing implementation registry and supplies its
input, output and config types. [`StepContext`](context.py) carries those parsed
values, the execution actor, record access, identity and checkpoint state. Domain
code executes under the actor's permissions. It must keep external effects out
of database steps.

[`WorkflowRunManager` and `StepRunManager`](managers.py) own admission, claims,
results, cancellation and recovery. A database step holds its run lock for its
whole transaction. The domain writes, fenced result and successor planning
commit together. After commit, every ready row of that run is sent to the job
queue, including parallel branches whose earlier message encountered a busy run.
The shared-worker tick wakes due waits and redispatches undelivered ready rows.
Delivery is at least once; step implementations must tolerate repeated execution.

Resource rows supply `key`, `name`, `subject_model`, `draft` and `publish`.
[`WorkflowDefinitionResource`](resources.py) delegates to `install_definition`,
which uses the same draft and publication owners as direct calls. Tests compose
the abstract sources through [`workflows.testing`](testing/__init__.py) and can
execute those same manager verbs synchronously through its drivers.
