# Workflow publications, results, and native calls

`Workflow` is a mutable lineage head. `WorkflowManager` applies checked
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

[`engine`](engine.py) is the public function facade for workflow operations.
Its domain owners enforce the operation contracts. Operations accepting a run,
decision, or step take the retained model instance. The existing public
`advance_dispatch`, `execute_dispatch`, `deliver_artifact_dispatch`,
`cancel_run_dispatch`, and `settle_run_dispatch` signatures delegate to
`WorkflowDispatch.objects.deliver`, as do durable task transport and the
synchronous test driver. Transport supplies a complete `WorkflowDispatchEnvelope`
for validation under the delivery locks.

Each member of the closed dispatch enum has one spec owning target selection,
lock order, constraint shape, handler selection, and consumption results. Adding
a kind requires its enum member and spec; scheduling policy belongs to the
manager verb that admits that intent. The spec does not define scheduling policy.

`GateResumeState` in [`attempts.py`](attempts.py) owns retained gate checkpoint
fields. Its typed attributes preserve the established `_resume_after_decisions`
and `_decision_*` storage keys, which are reserved. Custom operation checkpoint
values survive admission and settlement. `from_checkpoint()` reads these reserved
keys and reports malformed gate data as Django validation errors; only GateStep
resumption requires its retained `state` to be an object.

Resource files declare separate native Workflow, Step, and Edge rows, in dependency
order. Every Step names its `step_class` explicitly; the model has no fallback
operation. Test-only or consumer operations belong in their own composed registry.
The models select [`WorkflowDefinitionResource`](resources.py), a native
import-export adapter. Its cleaned instances feed `WorkflowManager.install_definition`,
which composes the definition snapshot and edit commands. Native row outcomes,
adoption and the canonical resources ledger remain authoritative. One lock-only
preflight acquires every affected existing workflow in primary-key order before
any dataset writes, including heads reached through other addons, adoption and
omitted rows. It does not import declarations or retain write authority.

Omission is scoped to the exact source addon, source path and target model. A
facet removes its omitted targets and ledgers without removing other contributions.
Remove incident Edge declarations before omitting their Step; an attempted
cross-facet cascade fails and rolls back the load. Similarly, a Workflow with
remaining children, incoming workflow references or publication history cannot be
removed by omission. Empty facet files must still identify their target model.
Omitted config on an unchanged step class preserves operator-authored config;
a changed class uses its defaults, and explicit config is canonicalized by the
Step owner. Explicit null remains distinct from omission and must satisfy the
model field's contract.

Xrefs can cross addons and earlier datasets. A Workflow may also reference an
earlier new Workflow row in the same dataset; an unresolved forward reference
receives the native row error, so declare its target first. Imported M2M fields
are unsupported for definition facets and fail explicitly.

The resources transaction owns rollback of rows, ledgers, grants and draft
revisions, including dry runs. Requested publication runs through the final
`after_resource_load` chain after every selected row and grant import. Reinstalling
the same graph retains the existing publication id. Dry runs retain the loader's
existing policy of skipping post-load hooks and rolling back all import writes.

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

Terminal transitions with a registered subject retain one `RUN_SETTLE` intent
in their own transaction. Addons register database-only subject handlers through
`ANGEE_WORKFLOW_SUBJECT_SETTLERS:append`, mapping an explicit model-class import
path to a callable import path. Abstract declarations expand to their installed
concrete content types; overlapping declarations fail at startup. A subject settler
accepts `(run, *, using=None)`, locks its subject on that alias, and guards
settlement against a newer operation on the subject. Delivery commits
the handler's writes and consumes the intent together; failures leave it pending
for the existing dispatch publisher. Subjectless runs and unregistered subjects
create no settlement intent. `settlement.rebuild_subject_settlers()` builds the
handler map at app startup and rebuilds it when Django settings change.

Long STANDARD steps compose `StepImpl.heartbeat_during(step_run, using=alias)`
around bounded external I/O. It refreshes only the captured attempt lease on a
separate connection and shares the reaper's configured heartbeat timeout. The
step's page transaction remains separate from attempt finalization.

`join_continuation` passes the child id and current invocation lease explicitly
to `StepAttemptManager`. The manager checks the retained parent relationship,
starter class, execution lineage and actor scope. It subscribes to that original
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
  expected_subject: example.documentsource
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
    required: [document_id]
    properties: {document_id: {type: string}}
  outcome: completed
  artifacts:
    - model: example.Document
      id_path: [document_id]
      label: Accepted document
```

Replay is denied by default. An operation that can prove fresh execution safe
declares `replay_mode = RecoveryMode.FRESH`; operations with provider-specific
uncertainty continue to override `recovery_capability()` from that adapter's
native contract. `DATABASE_COMMAND` no longer implies replay eligibility.

## Review gates and apply operations

`GateStep` is the only workflow review gate. Its static and bound forms share
the same `GateConfig`: `one_done`, `all_success`, `all_done`, `majority`, and
`sequential` policies; static or input-bound slots; input-bound `payload`,
`decision_schema`, `targets`, `record_access`, and `clean`; and optional
same-step resumption. A true `clean` value returns the
canonical empty `DecisionGateOutput` on `completed` without creating Decisions.
`resume: true` retains caller state, wakes the same `StepRun` after settlement,
and lets `GateStep.resumption()` return both the canonical gate output and the
per-slot runtime results. Bindings evaluate only against that invocation's
admitted input. Assemble workflow input, prior step outputs, or map items into
the gate's `input_binding` first; the gate never queries graph sources again.

Decision UI contracts are authored in Python with
`build_decision_action()`. Consumers provide `ReviewAction` declarations,
editable property schemas, and typed `ReviewFact` and `ReviewRecordReference`
values. The builder owns the closed
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
  action: review_document
  slots:
    - assignees: [angee/role:admin#member]
  actions:
    - {value: approve, label: Approve, verdict: COMPLETE, variant: primary}
    - {value: reject, label: Reject, verdict: REJECT, variant: destructive, confirm: Reject this document?}
  properties:
    reason: {type: string, title: Reason}
```

[DecisionContextFields](schema.py) exposes workflow and step context on
authorized Decisions; journal references retain independent read checks.

Frontend review content composes `WorkflowDecisionScaffold` for a domain-owned
frozen schema or `NativeWorkflowDecisionScaffold` for native facts and references.
The native scaffold parses and renders the retained context, selects the initial
peek, and keeps correction controls available when context is missing. Its
memoized `select` callback supplies one domain value to summaries and reference
presentation; keep the selector stable to reuse its parse across renders.
`ApprovalTask` owns action selection and submission. A content component may
localize action labels with `actionPresentation: { namespace, keyPrefix }`;
`${keyPrefix}.${action}` resolves against the addon's composed i18n bundle.
Missing translations retain the frozen schema label; verdicts, confirmations,
and submitted action values always come from that schema.

A consumer pairs the gate with a `DecisionApplyStep` subclass. Declare
`input_model`, `output_model`, `outcomes`, `effect`, `execution_mode`, and
`idempotent`, plus a narrower `gate_step_class` when needed. The base loads the
nearest matching executed predecessor gate's Decision through retained execution
ancestry, excluding skipped rows, requiring one gate at that depth, and calls
`invoke_command(step_run, *, decision_id, actor, now)`.
The nearest gate wins, with no fallback to a deeper gate, by deliberate contract.
Actorless expiry and timer resolutions pass `actor=None`; a command must explicitly accept
its expected terminal verdict. Clean gates have no Decision, and multi-slot
applications must choose their own domain operation over the retained collection.

`DecisionManager.decide(decision_id, *, actor, resolution=DecisionSubmission(...))`
owns submission authorization, schema validation, settlement, grant removal and
durable dispatch as one atomic operation. JSON-authored schemas validate with
Draft 2020-12 without coercion or inserted defaults; Python-authored contracts
keep native Pydantic validation.

For application, `DecisionManager.locked_resolution` validates the retained
Decision, matching resolver, expected action/verdict and current consumer under
ancestry locks. A bridge calls public domain verbs with plain values and the
acting user. Those verbs lock domain rows, enforce their own REBAC and expected
state, and use durable unique/conditional facts for idempotence. Keep dependency
direction explicit: `parties` never imports workflows; `workflows_parties` only
orchestrates both owners. An addon already depending on workflows can accept a
Decision identity in its command, as `ExtractionManager.revise_from_decision`
does. `DATABASE_COMMAND` commits the operation,
result and dispatch together; provider and blob I/O stay outside this mode.

The exact resource shape for a bound gate and apply pair is:

```yaml
- xref: document_review_gate
  fields:
    workflow: example.document_review
    key: review
    name: Review document
    step_class: gate
    input_binding: {kind: step_output, step_key: prepare_review, path: []}
    config:
      policy: all_done
      action: review_document
      slots: {kind: workflow_input, path: [slots]}
      payload: {kind: workflow_input, path: [payload]}
      decision_schema: {kind: workflow_input, path: [decision_schema]}
      targets: {kind: workflow_input, path: [targets]}
      record_access: {kind: workflow_input, path: [record_access]}
      clean: {kind: workflow_input, path: [clean]}
    join_rule: all_success
    is_entry: false
- xref: document_review_apply
  fields:
    workflow: example.document_review
    key: apply_review
    name: Apply document review
    step_class: example_document_review_apply
    input_binding: {kind: step_output, step_key: review, path: []}
    config: {}
    join_rule: all_success
    is_entry: false
```

`prepare_review` imports [`GateBinding`](configs.py) from
`angee.workflows.configs` and declares `output_model = GateBinding`. It returns
the model's `model_dump(mode="json")` through `StepResult.done`; the gate binds
its six resolved fields as shown above. The producer builds `payload` and
`decision_schema` with `build_decision_action()`, or returns
`GateBinding(clean=True)` when no review is needed. The apply class is
a registered `DecisionApplyStep`; the graph connects `review.completed` to
`apply_review`. If `clean` can be true on that edge, the apply subclass must
recognize the canonical empty gate output before calling the base and return a
no-mutation result; the one-resolution base is entered only for a real review.
The top-level key `kind` is reserved for the workflow binding discriminator in
static mapping-valued gate fields; put domain data with that name below another
payload key. A clean binding is evaluated before any decision-authoring binding,
so the clean branch does not require non-clean payloads or slots to exist.

Application lock order is workflow ancestry, Decision, then domain rows. The
persisted invocation lease comes from `step_run.current_attempt.lease_token`;
it is never transported through an ambient session or dynamic step-run attribute.
