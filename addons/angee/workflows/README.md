# Workflow publications, results, and native calls

`Workflow` is a mutable lineage head. `WorkflowDefinitionManager` applies checked
definition edits to it and publishes immutable versions. Its public
`input_schema` validates the JSON supplied to an invocation before a run is
retained. The entry operation's `input_model` validates a later step input after
any `input_binding` has transformed the workflow input. `output_schema` validates
one terminal result, selected by `result_rules`. A rule names an ordinary
terminal producer, its declared `when_outcome`, one business `outcome`, and an
existing binding tree. A successful `WorkflowRun.result` retains that outcome
and validated output with the terminal state. Failed and canceled runs retain
the reserved `failed` and `canceled` envelopes. The result is immutable after
the terminal transition. Publication checks terminal producer routing choices
for co-applicability with a bounded path traversal; a graph whose uniqueness
cannot be proved within that bound is rejected. Binding checks prove required
source paths and simple JSON Schema shapes, and reject unsupported constraints
conservatively. Completion validates the exact JSON output again.

Resource files continue to declare native Workflow, Step, and Edge rows.
Those models hand their row groups to the Workflow owner, which compares the
canonical declaration against the saved draft, calls `apply_definition` only
when it changed, and calls `publish_definition` for requested publications.
The normal resource transaction, xref ledger, and dry-run rollback still own
the load. Reinstalling the same graph retains the existing publication id.

The built-in operation key is `call_workflow`. Its input selects an exact
published child, child subject, and child input. A static config pins a public
publication id; a dynamic config declares `expected_input_schema`,
`expected_output_schema`, `expected_subject`, and `expected_outcomes`. The
parent call's original StepRun is the exact retained child slot, including a
FRESH recovery. The call waits on the child through its current StepAttempt's
external target. A child terminal transition commits an artifact-delivery
intent targeting the child; delivery independently locks the parent run,
parent step, then current attempt and wakes every exact current call subscribed
to that child. This avoids taking a parent lock inside a child terminal
transaction and closes the child-finished-before-wait race.

`WorkflowRun.parent_relation` is an immutable invocation fact. `owned_call`
means the parent owns cancellation of its active child; `continuation` retains
ancestry and retry identity but has its own lifetime after handoff. Parent
cancellation commits child-targeted cancel intents with its own terminal state.
Their delivery locks parent before child and remains valid after the parent is
terminal. A child's `child_failed` or `child_canceled` call outcome has
`StepRun.output_present=false`; parent definitions may route those outcomes,
and output bindings on those routes are rejected. A Map body of calls completes
only after each item's child result is complete and joins results in item order.
