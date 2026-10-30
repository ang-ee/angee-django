---
name: workflow-definitions
description: "Authors a workflow in an Angee addon: Step classes registered through ANGEE_WORKFLOW_STEP_CLASSES, DATABASE vs IO boundaries, decision steps, the definition as a workflows.workflow resource via install_definition, record_changed triggers, RunSubject and tests with the workflows testing drivers."
---

# Workflow Definitions

The reference example is `examples/addons/example/notes` (steps, rows, trigger, tests); `angee.workflows` owns execution.

## Use when

- "Add a workflow / automate `<verb>` on `<Model>` when `<condition>`."
- "Write a step for the `<name>` workflow", "register step `<key>`", "ask a reviewer / hold for approval in a workflow."
- "Ship the workflow definition as a resource / trigger it on record save."
- Not for the question-and-answer card itself (`angee.decisions` README) or for page work (`angee-pages`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/steps.py` | `Step` subclasses: key, typed input/output/config, mode, outcomes, body | `examples/addons/example/notes/steps.py:28,47,65` |
| `<addon>/autoconfig.py` | `ANGEE_WORKFLOW_STEP_CLASSES.<key>` entries | `examples/addons/example/notes/autoconfig.py:5` |
| `<addon>/resources/<tier>/NNN_workflows.workflow.yaml`, `…workflows.trigger.yaml` | the graph document and its disabled trigger policy | `examples/addons/example/notes/resources/demo/100_workflows.workflow.yaml`, `110_workflows.trigger.yaml` |
| `addons/angee/workflows/steps.py` | `Step` (:226), `StepMode` (:40), `RetryPolicy` (:48), settlements `Done`/`Wait`/`NextPage`/`Fail`/`Ask` (:84,:113,:147,:160,:185), `resolve_step` (:358) | module docstring |
| `addons/angee/workflows/context.py` | `StepContext` (:24): `subject_for_update` :73, `load` :87, `watch` :116, `ask` :129, `record` :162, `start_run` :178 | docstrings |
| `addons/angee/workflows/decision_steps.py` | `apply_proposals` (:19), `DecisionStep` (:73), built-in `AskDecision` (:110) | docstrings |
| `addons/angee/workflows/definition.py`, `models.py`, `managers.py` | `Definition` (:274), `WorkflowExtension` (:147); `Workflow` (:145), `WorkflowVersion` (:220), `Trigger` (:872); `WorkflowManager.install_definition` (:363), `save_draft` (:277), `publish` (:309), `WorkflowRunManager.start` (:584) | [README](../../README.md) |
| `addons/angee/workflows/resources.py` | `WorkflowDefinitionResource` (:68), `TriggerResource` (:121), `WorkflowExtensionResourceKind` (:21) | docstrings |
| `addons/angee/workflows/triggers.py`, `subjects.py` | `TriggerSource` (:187), `RecordChangedOptIn` (:178), `TriggerGrantTarget` (:72), `RecordChanged` (:389); `RunSubject` (:10) | docstrings |
| `addons/angee/workflows/testing/` | `register_steps` :53, `load_workflow` :70, `publish_draft` :97, `start_run` :110, `decide` :117, `run_until` :124, `RunFactory` :147 (`drivers.py`); fixtures `register_step`, `execution`, `run_factory` (`fixtures.py:37,27,48`) | `testing/__init__.py` docstring |

## Reuse first

- The built-in steps before a new one: `ask_decision`, `map` (`addons/angee/workflows/maps.py:52`), `await_run` (`awaits.py:61`).
- `StepContext` verbs for every record touch: `ctx.subject` / `ctx.subject_for_update()`, `ctx.load(model, id, permission=, lock=)`,
  `ctx.record(record, operation=)`, `ctx.watch(record)`, `ctx.note(message)`; never a raw `Model.objects` read in a body.
- The subject model's own verbs for domain work (`Note.publish`, `Note.publication_summary`,
  `examples/addons/example/notes/models.py:93,113`); a step dispatches, it does not decide.
- `DecisionRequest`, `DecisionProposal`, `DecisionContext`, `DecisionFact` (`addons/angee/decisions/contracts.py`);
  `ImplBase` registry mechanics (`Step.registry_setting`); `angee.jobs.timeouts.task_time_budget` for IO bounds.
- Existing addon steps as shapes: `addons/angee/workflows_parties/steps.py`, `workflows_integrate/steps.py`, `workflows_agents/steps.py`.

## Steps

1. **Declare the dependencies.** `pyproject.toml` `[project].dependencies` lists `angee-workflows`, `angee-decisions` when a step asks, and
   `angee-provenance` (`examples/addons/example/notes/pyproject.toml:8-24`). **Owner rule:** the manifest owns dependency facts
   ([composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).

2. **Write the step in `<addon>/steps.py`.** Type parameters are the input, output and config models (`None` accepts JSON);
   `key` is the registry key, `subject` the model label a run must be about.

   ```python
   class ValidateNotePublication(Step[None, NotePublicationOutput, None]):
       """Check publication readiness through the note's own contract."""

       key = "note_validate_publication"
       label = "Validate note"
       category = "Activity"
       subject = "notes.note"
       outcomes = {"needs_review": "Needs review", "ok": "Ready"}

       def run(self, ctx: Any) -> Done:
           note = ctx.subject
           return ctx.done(note.publication_summary(), outcome="needs_review" if note.reviewer_id else "ok")
   ```

   Every outcome a body returns must be in `outcomes` (`error` is added for routing, `steps.py:316`); `required_outcomes`
   names the ones a graph must route. **Owner rule:** the `Step` owns schema projection and settlement validation
   (`Step.check`, `steps.py:340`); the record owns the rule the body reads, so business branching on subject fields is a
   model method ([put behavior on the owning object](../../../../../docs/guidelines.md#put-behavior-on-the-owning-object)).

3. **Choose the transaction boundary.** `mode = StepMode.DATABASE` (default, 30 s per statement) runs inside the run's
   transaction under the actor: lock with `ctx.subject_for_update()` / `ctx.load(lock=True)`, register `ctx.watch(record)`,
   return `ctx.wait(until=, state=)` to resume later — `AwaitNoteReview` (`notes/steps.py:47`). `mode = StepMode.IO`
   (5 min default) runs after the claim commits, outside every transaction: key external calls on `ctx.idempotency_key`,
   call `ctx.begin_effect()` before the first effect and `ctx.heartbeat()` during long work, declare `effect_idempotent = True`
   only when a repeat is safe, and set `retry = RetryPolicy(max_attempts=, backoff=)` with `raise Retryable(...)` for
   transient failures; an IO `timeout` must stay below `task_time_budget()` (`steps.py:371`). **Owner rule:**
   [Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts) fixes the boundary: database steps
   keep external I/O out; IO bodies cannot roll back their writes.

4. **Register the key in `<addon>/autoconfig.py`.**

   ```python
   SETTINGS = {
       "ANGEE_WORKFLOW_STEP_CLASSES.note_publish": "example.notes.steps.PublishNote",
       "ANGEE_WORKFLOW_STEP_CLASSES.note_await_review": "example.notes.steps.AwaitNoteReview",
   }
   ```

   `check_step_registry` (registered by `addons/angee/workflows/apps.py:20`) validates every entry; `resolve_step` is the only
   resolver. **Owner rule:** [Autoconfig](../../../../../docs/composer.md#autoconfig) merges contributions; no addon-local step table.

5. **Ask a question with a decision step.** Subclass `DecisionStep[I, O, C]` (DATABASE only, `decision_steps.py:76`), implement
   `ask(ctx)` returning `ctx.ask(DecisionRequest(kind=, requester=ctx.actor, records=, assignees=, proposal=, context=))` and map
   each alternative's `outcome` into `outcomes`; override `continue_with` only to shape typed output — `ReviewNotePublication`
   (`notes/steps.py:65`). `apply_proposals` applies the verdict's record actions in the resumed body before `continue_with`
   (`decision_steps.py:81-86`); a config-only question is the built-in `ask_decision` node. **Owner rule:** decisions owns the
   question and verdict; record owners apply the chosen actions as the run actor
   ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts), "Reviews retain one current decision per step").

6. **Author the definition as a resource.** `resources/<tier>/NNN_workflows.workflow.yaml` rows carry `key`, `name`,
   `description`, `subject_model`, `publish` and the `draft` graph; list the file under `[tool.angee.resources]` with `adopt = "key"`.

   ```yaml
   _meta: {model: workflows.Workflow}
   rows:
     - xref: note_publish
       fields:
         key: note-publish
         name: Note publication
         subject_model: notes.note
         publish: true
         draft:
           nodes:
             validate: {step: note_validate_publication, next: {needs_review: review, ok: publish}}
             review: {step: note_review_publication, next: {approved: publish}}
             publish: {step: note_publish, input: {from: [review, validate]}}
           results:
             - {from: publish}
             - {from: review, when: [rejected]}
   ```

   A `map` node takes `body: {step: …}`; `await_run` takes `config: {expects: <child key>}` and routes the child's outcomes.
   Another addon extends a graph additively through a `workflows.workflowextension` entry (`resources.py:17`), never by editing
   the base row. **Owner rule:** `WorkflowDefinitionResource.save_instance` delegates to `install_definition`, the same
   `save_draft`/`publish` verbs as the console (`managers.py:363`); `Definition` validates the graph ([README](../../README.md)).

7. **Trigger it on record changes (optional).** The subject opts in with `RecordChangedOptIn` and declares its grant scope:

   ```python
   class Note(RecordChangedOptIn, OwnerMixin, AuditMixin, ThreadedModelMixin, HistoryMixin, AngeeDataModel):
       @classmethod
       def record_changed_grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
           return (TriggerGrantTarget(ObjectRef("notes/role", "trigger_editor"), "member", "effective_member",
                                      ObjectRef("angee/role", "admin")),)
   ```

   The trigger row (`110_workflows.trigger.yaml`) names `workflow: example.notes.note_publish`, `source: record_changed`,
   `model_label` and a Hasura-style `condition`; it installs disabled (`TriggerResource`, `resources.py:121`) and an eligible
   user enables it in the console. **Owner rule:** the source declares grant targets and the model its own scope; enablement
   writes tuples to the workflow principal ([REBAC](../../../../../docs/backend/guidelines.md#rebac); `workflows.E001`, `triggers.py:436`).

8. **Own the root-run lifecycle on the record (optional).** Compose `RunSubject`; implement `admit_run(run)` and `settle_run(run, status)`
   with database work only (`addons/angee/workflows/testing/models.py:13`); `workflows.E002` rejects a partial opt-in (`subjects.py:38`).
   **Owner rule:** the record, not a signal receiver, owns admission and settlement ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts)).

9. **Test through the drivers.** Framework addons: `tests/test_<name>.py` with `angee.workflows.testing` installed
   (`tests/settings.py:101`) and the `register_step`, `execution`, `run_factory` fixtures. Consumer addons: a `TransactionTestCase`
   in the addon's `tests/` (`examples/addons/example/notes/tests/test_workflow_steps.py:35`): `load_workflow("example.notes.note_publish",
   actor=admin, allow_non_dev=True)`, `start_run(workflow, actor=, subject=)`, `run_until(run)`, `decide(decision, actor=, chosen=[...])`,
   `run_factory(workflow, actor=).at("review")`, inside `capture_tasks()`. **Owner rule:** the drivers compose production admission
   and transition verbs; nothing fabricates step rows ([source-addon test models](../../../../../docs/checks.md#source-addon-test-models)).

## Do not

- External I/O (HTTP, SMTP, a bridge call) inside a `DATABASE` body: an `IO` step keyed on `ctx.idempotency_key` ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts)).
- An addon-local step table, dotted-path resolver or `import_string` of a step: `ANGEE_WORKFLOW_STEP_CLASSES` plus `resolve_step` (`steps.py:358`; [Autoconfig](../../../../../docs/composer.md#autoconfig)).
- Business branching on the subject's columns inside `run`: a model or queryset method the step calls ([put behavior on the owning object](../../../../../docs/guidelines.md#put-behavior-on-the-owning-object)).
- `Workflow.objects.create(...)`, a hand-set `published`, or an edited `WorkflowVersion.document`: `install_definition` / `save_draft` / `publish` ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts), "`Definition` owns graph policy").
- Catching retries, timeouts or validating settlement values in the body: `Retryable`, `RetryPolicy`, `timeout` and `Step.check` own them (`steps.py:32,48,340`).
- Applying a verdict's actions yourself in `continue_with`: `apply_proposals` already ran in the body transaction (`decision_steps.py:81-90`).
- Reading a record in a body through `Model.objects` or `system_context`: `ctx.load` / `ctx.subject` under the run actor (`context.py:87`).
- `enabled: true` on a trigger row or a trigger enabled by code: `TriggerResource` clears activation (`resources.py:121`); enablement is operator state ([Glossary: Trigger source grant](../../../../../docs/glossary.md#backend)).
- A caveated subject in a grant target's zed: `angee.E024` (`addons/angee/base/checks.py:37`); field-backed conditional relations.

## Verify

Done means: system checks are clean (`check_step_registry`, `workflows.E001`, `workflows.E002`), the resource load
leaves `Workflow.published` set, `run_until` reaches a terminal `WorkflowRun` with the expected outcome and output,
and a decision test answers through `decide` and resumes the step.

- In a lane (jj workspace: no host, database or JS install): the focused pytest module, ruff and mypy on touched files,
  `tests/test_layering.py` — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: the composed-host run of consumer tests (`tests/test_composed_note_workflow.py`), the stack
  resource load, permission sync and console SDL/codegen — [composition and schema](../../../../../docs/checks.md#composition-and-schema).
- Loop: [slice](../../../../../.agents/skills/slice/SKILL.md#workflow). False greens: `run_until` returns a *waiting* run without
  failing, so assert `run.status`; `capture_tasks` proves a send was registered, not that a worker ran (`testing/__init__.py`).

## Pitfalls

- Changing `mode` without `timeout` selects that mode's default; an IO timeout at or above the worker budget is refused at resolve time (`steps.py:244-250,371`).
- A `Done` with an outcome missing from `outcomes` fails the attempt at the body boundary (`steps.py:90-94`).
- Delivery is at least once; bodies must tolerate repeated execution and a non-idempotent uncertain effect waits for an operator ([README](../../README.md)); a wait deadline must be an aware datetime (`steps.py:120`).
- A changed child outcome contract requires affected parents to be republished (`await_run`; [Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts)).
- A human-published version runs as the workflow principal only while its publisher can delegate every enabled trigger grant ([Glossary: Workflow principal](../../../../../docs/glossary.md#backend)).

## Read next

- [Workflow pitfalls](references/pitfalls.md)
- [Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts), [REBAC](../../../../../docs/backend/guidelines.md#rebac), [Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives)
- [Glossary: Workflows](../../../../../docs/glossary.md#workflows), [Glossary: Decisions](../../../../../docs/glossary.md#decisions), [Workflows README](../../README.md), [Decisions README](../../../decisions/README.md), [Stack](../../../../../docs/stack.md#backend)
- Skills: `angee-design`, `angee-resources`, `angee-permissions`, `angee-tests`, `slice`

## Sources

<!--
Docs: docs/backend/guidelines.md #workflow-execution, #workflow-contracts, #rebac; docs/glossary.md #backend, #workflows, #decisions; docs/checks.md #choose-the-working-directory, #python-and-composition-contracts, #source-addon-test-models, #browser-verification, #what-ci-actually-runs; docs/composer.md #autoconfig.
Code: addons/angee/workflows/{README.md, steps.py:1-376, models.py:145-260,872-895, managers.py:363-405, resources.py, autoconfig.py, pyproject.toml, apps.py, context.py:20-110, decision_steps.py:1-100, subjects.py, triggers.py:175-200,429-436, definition.py:147,181,274, maps.py:13-52, awaits.py:15-61, testing/__init__.py, testing/drivers.py, testing/fixtures.py, testing/models.py:1-30}; addons/angee/decisions/README.md; examples/addons/example/notes/{pyproject.toml, autoconfig.py, steps.py, models.py:22-45, resources/demo/100_workflows.workflow.yaml, 110_workflows.trigger.yaml, tests/test_workflow_steps.py:1-70}; tests/workflow_steps.py:1-80; tests/test_workflows_testing.py:1-60; tests/test_composed_note_workflow.py; tests/settings.py:56-101.
Mismatches: none found for the cited surfaces; no doc lists the YAML node keys (step/next/input/config/body/results/when) — derived from the notes rows and Definition.
-->
