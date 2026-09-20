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

## Review gates and apply operations

`GateStep` is the only workflow review gate. Its static and bound forms share
the same `GateConfig`: `one_done`, `all_success`, `all_done`, `majority`, and
`sequential` policies; static or input-bound slots; input-bound `payload`,
`decision_schema`, `targets`, `record_access`, and `clean`; target authority
paths; and optional same-step resumption. A true `clean` value returns the
canonical empty `DecisionGateOutput` on `completed` without creating Decisions.
`resume: true` retains caller state, wakes the same `StepRun` after settlement,
and lets `GateStep.resumption()` return both the canonical gate output and the
per-slot runtime results. Bindings evaluate only against that invocation's
admitted input. Assemble workflow input, prior step outputs, or map items into
the gate's `input_binding` first; the gate never queries graph sources again.

Decision UI contracts are authored in Python with
`build_decision_action()`. Consumers provide `ReviewAction` declarations,
editable property schemas, and typed `ReviewFact`, `ReviewRecordReference`,
`ReviewDifference`, and `ReviewReason` values. The builder owns the closed
tagged `oneOf`, action metadata, and read-only context schema. The Decision
manager remains the sole compiler when it admits the suspension; consumers do
not compile or hand-author action branches.

A consumer pairs the gate with a `DecisionApplyStep` subclass. The subclass
declares `input_model`, `output_model`, `outcomes`, `effect`, `execution_mode`,
and `idempotent`, plus its predecessor `gate_step_class` when it is narrower
than `GateStep`. Its `locked_record_basis()` locks every row the verb may
mutate. The base loads the one direct predecessor Decision, resolves its human
actor, consumes the exact admitted resolution and provenance through
`engine.consume_decision_resolution()`, and then calls `apply_resolution()`.
The adapter returns its manager verb's `StepResult` unchanged, including a
durable wait. Use `DATABASE_COMMAND` only when the complete operation is local
database work; provider and blob I/O remain a standard or external operation.

The exact resource shape for a bound gate and apply pair is:

```yaml
- xref: invoice_review_gate
  fields:
    workflow: intake.invoice_review
    key: review
    name: Review invoice
    step_class: gate
    input_binding: {kind: step_output, step_key: prepare_review, path: []}
    config:
      policy: all_done
      action: review_invoice
      slots: {kind: workflow_input, path: [slots]}
      payload: {kind: workflow_input, path: [payload]}
      decision_schema: {kind: workflow_input, path: [decision_schema]}
      targets: {kind: workflow_input, path: [targets]}
      record_access: {kind: workflow_input, path: [record_access]}
      clean: {kind: workflow_input, path: [clean]}
    join_rule: all_success
    is_entry: false
- xref: invoice_review_apply
  fields:
    workflow: intake.invoice_review
    key: apply_review
    name: Apply invoice review
    step_class: intake_invoice_review_apply
    input_binding: {kind: step_output, step_key: review, path: []}
    config: {}
    join_rule: all_success
    is_entry: false
```

`prepare_review` returns the six bound fields above. It builds `payload` and
`decision_schema` with `build_decision_action()`; each target is
`{model, id, tab?, authority_path?, authority_gate_path?}`, and each record
access item is the native `DecisionRecordAccess` JSON shape. The apply class is
a registered `DecisionApplyStep`; the graph connects `review.completed` to
`apply_review`. If `clean` can be true on that edge, the apply subclass must
recognize the canonical empty gate output before calling the base and return a
no-mutation result; the one-resolution base is entered only for a real review.
The top-level key `kind` is reserved for the workflow binding discriminator in
static mapping-valued gate fields; put domain data with that name below another
payload key. A clean binding is evaluated before any decision-authoring binding,
so the clean branch does not require non-clean payloads or slots to exist.

The apply base passes its record-basis loader into Decision consumption. The
manager locks the run, step run, and current attempt before invoking that loader,
then validates the consumed Decision against the resulting records. Database
commands retain those locks through their surrounding invocation transaction;
adapters must not acquire domain rows before calling the base.
