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
Every Step names its `step_class` explicitly; the model has no fallback
operation. Test-only or consumer operations belong in their own composed
registry and never become a framework default.
Those models hand their row groups to the Workflow owner, which compares the
canonical declaration against the saved draft, calls `apply_definition` only
when it changed, and calls `publish_definition` for requested publications.
The normal resource transaction, xref ledger, and dry-run rollback still own
the load. Reinstalling the same graph retains the existing publication id.

The built-in operation key is `call_workflow`. A static config pins a public
publication id. A keyed config declares `workflow_key` plus
`expected_input_schema`, `expected_output_schema`, `expected_subject`, and
`expected_outcomes`; it selects the current publication only when creating a
new child slot. Dynamic input selection declares the same expected contract.
Resume and FRESH recovery first resolve the retained child, validate that
child's publication, input, subject, actor, and outcomes, and ask the run
manager to admit the exact retained invocation. They never reselect currency.
The call waits on the child through its current StepAttempt's external target.
A child terminal transition commits an artifact-delivery intent targeting the
child; delivery independently locks the parent run, parent step, then current
attempt and wakes every exact current call subscribed to that child.

A keyed call has this shape:

```yaml
step_class: call_workflow
input_binding:
  kind: object
  fields:
    input: {kind: step_output, step_key: prepare, path: [request]}
    subject: {kind: step_output, step_key: prepare, path: [subject]}
config:
  workflow_key: document_extraction
  expected_input_schema: {type: object}
  expected_output_schema: {type: object}
  expected_subject: storage.file
  expected_outcomes: [processed, source_hold]
```

`WorkflowRun.parent_relation` is an immutable invocation fact. `owned_call`
means the parent owns cancellation of its active child; `continuation` retains
ancestry and retry identity but has its own lifetime after handoff. Parent
cancellation commits child-targeted cancel intents with its own terminal state.
Their delivery locks parent before child and remains valid after the parent is
terminal. A child's `child_failed` or `child_canceled` call outcome has
`StepRun.output_present=false`; parent definitions may route those outcomes,
and output bindings on those routes are rejected. A Map body of calls completes
only after each item's child result is complete and joins results in item order.

`join_continuation` consumes a continuation id only through the provenance-bound
starter output admitted by `StepAttemptManager`. It subscribes to that original
run before reading completion, treats terminal artifact delivery as the primary
wake, and retains `reconcile_after` as a bounded missed-delivery check. An exact
successful FRESH recovery may satisfy the join only through the manager's
schema, subject, actor, input, lineage, and result checks.

```yaml
step_class: join_continuation
input_binding: {kind: step_output, step_key: handoff, path: []}
config:
  child_id_path: [continuation_id]
  expected_starter_class: start_continuation
  expected_output_schema: {type: object}
  expected_subject: accounting_intake.invoicesource
  expected_outcomes: [completed]
  reconcile_after: 900
```

`emit` projects its admitted input through a declared output schema. Each artifact
binding selects a guaranteed string path, resolves that public id through the
execution actor's read scope, and retains the artifact beside the result. A
definition may use that projection as a result-rule producer or route it onward.

```yaml
step_class: emit
input_binding: {kind: step_output, step_key: finalize, path: []}
config:
  output_schema:
    type: object
    required: [invoice_id]
    properties: {invoice_id: {type: string}}
  outcome: completed
  artifacts:
    - model: accounting_intake.Invoice
      id_path: [invoice_id]
      label: Accepted invoice
```

Replay is denied by default. An operation that can prove fresh execution safe
declares `replay_mode = RecoveryMode.FRESH`; operations with provider-specific
uncertainty continue to override `recovery_capability()` from that adapter's
native contract. `DATABASE_COMMAND` no longer implies replay eligibility.

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

Static YAML gates declare fixed `actions` and optional editable `properties`.
The normalized definition retains only those authoring declarations;
`GateConfig.admission_decision_schema()` feeds them to
`build_decision_action()` when the Decision suspension is admitted, and that
Decision freezes the compiled schema. A hand-written static `oneOf`, including
a slot-local replacement, is invalid.

```yaml
step_class: gate
config:
  policy: one_done
  action: review_invoice
  slots:
    - assignees: [angee/role:admin#member]
  actions:
    - {value: approve, label: Approve, verdict: COMPLETE, variant: primary}
    - {value: reject, label: Reject, verdict: REJECT, variant: destructive, confirm: Reject this invoice?}
  properties:
    reason: {type: string, title: Reason}
```

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
