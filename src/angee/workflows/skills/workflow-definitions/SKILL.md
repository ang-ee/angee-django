---
name: workflow-definitions
description: "Authors a workflow in an Angee addon: Step classes registered through ANGEE_WORKFLOW_STEP_CLASSES, DATABASE vs IO boundaries, decision steps, the definition as a workflows.workflow resource via install_definition, record_changed triggers, RunSubject and tests with the workflows testing drivers."
---

# Workflow Definitions

The reference example is `examples/src/example/notes` (steps, rows, trigger, tests); `angee.workflows` owns execution.

## Use when

- "Add a workflow / automate `<verb>` on `<Model>` when `<condition>`."
- "Write a step for the `<name>` workflow", "register step `<key>`", "ask a reviewer / hold for approval in a workflow."
- "Ship the workflow definition as a resource / trigger it on record save."
- Not for the question-and-answer card itself (`angee.decisions` README) or for page work (`angee-pages`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/steps.py` | `Step` subclasses: key, typed input/output/config, mode, outcomes, body | `ValidateNotePublication`, `AwaitNoteReview`, `ReviewNotePublication` in `examples/src/example/notes/steps.py` |
| `<addon>/autoconfig.py` | `ANGEE_WORKFLOW_STEP_CLASSES.<key>` entries | `SETTINGS` in `examples/src/example/notes/autoconfig.py` |
| `<addon>/resources/<tier>/NNN_workflows.workflow.yaml`, `…workflows.trigger.yaml` | the graph document and its disabled trigger policy | `examples/src/example/notes/resources/demo/100_workflows.workflow.yaml`, `110_workflows.trigger.yaml` |
| `src/angee/workflows/steps.py` | `Step`, `StepMode`, `RetryPolicy`, settlements `Done`/`Wait`/`NextPage`/`Fail`/`Ask`, `resolve_step` | module docstring |
| `src/angee/workflows/context.py` | `StepContext`: `subject_for_update`, `load`, `watch`, `ask`, `record`, `start_run` | docstrings |
| `src/angee/workflows/decision_steps.py` | `apply_proposals`, `DecisionStep`, built-in `AskDecision` | docstrings |
| `src/angee/workflows/definition.py`, `models.py`, `managers.py` | `Definition`, `WorkflowExtension`; `Workflow`, `WorkflowVersion`, `Trigger`; `WorkflowManager.install_definition`, `save_draft`, `publish`, `WorkflowRunManager.start` | [README](../../README.md) |
| `src/angee/workflows/resources.py` | `WorkflowDefinitionResource`, `TriggerResource`, `WorkflowExtensionResourceKind` | docstrings |
| `src/angee/workflows/triggers.py`, `subjects.py` | `TriggerSource`, `RecordChangedOptIn`, `TriggerGrantTarget`, `RecordChanged`; `RunSubject` | docstrings |
| `src/angee/workflows/testing/` | `register_steps`, `load_workflow`, `publish_draft`, `start_run`, `decide`, `run_until`, `RunFactory` (`drivers.py`); fixtures `register_step`, `execution`, `run_factory` (`fixtures.py`) | `testing/__init__.py` docstring |

## Reuse first

- The built-in steps before a new one: `ask_decision`, `map` (`Map` in `src/angee/workflows/maps.py`), `await_run` (`AwaitRun` in `awaits.py`).
- `StepContext` verbs for every record touch: `ctx.subject` / `ctx.subject_for_update()`, `ctx.load(model, id, permission=, lock=)`,
  `ctx.record(record, operation=)`, `ctx.watch(record)`, `ctx.note(message)`; never a raw `Model.objects` read in a body.
- The subject model's own verbs for domain work (`Note.publish`, `Note.publication_summary`,
  `examples/src/example/notes/models.py`); a step dispatches, it does not decide.
- `DecisionRequest`, `DecisionProposal`, `DecisionContext`, `DecisionFact` (`src/angee/decisions/contracts.py`);
  `ImplBase` registry mechanics (`Step.registry_setting`); `angee.jobs.timeouts.task_time_budget` for IO bounds.
- Existing addon steps as shapes: `src/angee/workflows_parties/steps.py`, `workflows_integrate/steps.py`, `workflows_agents/steps.py`.

## Steps

1. **Declare the dependencies.** `pyproject.toml` `[project].dependencies` lists `angee-workflows`, `angee-decisions` when a step asks, and
   `angee-provenance` (`[project]` in `examples/src/example/notes/pyproject.toml`). **Owner rule:** the manifest owns dependency facts
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

   Every outcome a body returns must be in `outcomes` (`error` is added for routing, `Step._with_error_outcome` in `steps.py`); `required_outcomes`
   names the ones a graph must route. **Owner rule:** the `Step` owns schema projection and settlement validation
   (`Step.check`, `steps.py`); the record owns the rule the body reads, so business branching on subject fields is a
   model method ([put behavior on the owning object](../../../../../docs/guidelines.md#put-behavior-on-the-owning-object)).

3. **Choose the transaction boundary.** `mode = StepMode.DATABASE` (default, 30 s per statement) runs inside the run's
   transaction under the actor: lock with `ctx.subject_for_update()` / `ctx.load(lock=True)`, register `ctx.watch(record)`,
   return `ctx.wait(until=, state=)` to resume later — `AwaitNoteReview` (`notes/steps.py`). `mode = StepMode.IO`
   (5 min default) runs after the claim commits, outside every transaction: key external calls on `ctx.idempotency_key`,
   call `ctx.begin_effect()` before the first effect and `ctx.heartbeat()` during long work, declare `effect_idempotent = True`
   only when a repeat is safe, and set `retry = RetryPolicy(max_attempts=, backoff=)` with `raise Retryable(...)` for
   transient failures; an IO `timeout` must stay below `task_time_budget()` (`resolve_step` in `steps.py`). **Owner rule:**
   [Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts) fixes the boundary: database steps
   keep external I/O out; IO bodies cannot roll back their writes.

4. **Register the key in `<addon>/autoconfig.py`.**

   ```python
   SETTINGS = {
       "ANGEE_WORKFLOW_STEP_CLASSES.note_publish": "example.notes.steps.PublishNote",
       "ANGEE_WORKFLOW_STEP_CLASSES.note_await_review": "example.notes.steps.AwaitNoteReview",
   }
   ```

   `check_step_registry` (registered by `WorkflowsConfig.ready` in `src/angee/workflows/apps.py`) validates every entry; `resolve_step` is the only
   resolver. **Owner rule:** [Autoconfig](../../../../../docs/composer.md#autoconfig) merges contributions; no addon-local step table.

5. **Ask a question with a decision step.** Subclass `DecisionStep[I, O, C]` (DATABASE only, `DecisionStep.__init_subclass__` in `decision_steps.py`), implement
   `ask(ctx)` returning `ctx.ask(DecisionRequest(kind=, requester=ctx.actor, records=, assignees=, proposal=, context=))` and map
   each alternative's `outcome` into `outcomes`; override `continue_with` only to shape typed output — `ReviewNotePublication`
   (`notes/steps.py`). `apply_proposals` applies the verdict's record actions in the resumed body before `continue_with`
   (`DecisionStep.run` in `decision_steps.py`); a config-only question is the built-in `ask_decision` node. **Owner rule:** decisions owns the
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
   Another addon extends a graph additively through a `workflows.workflowextension` entry (`WORKFLOW_EXTENSION_KIND` in `resources.py`), never by editing
   the base row. **Owner rule:** `WorkflowDefinitionResource.save_instance` delegates to `install_definition`, the same
   `save_draft`/`publish` verbs as the console (`managers.py`); `Definition` validates the graph ([README](../../README.md)).

7. **Trigger it on record changes (optional).** The subject opts in with `RecordChangedOptIn` and declares its grant scope:

   ```python
   class Note(RecordChangedOptIn, OwnerMixin, AuditMixin, ThreadedModelMixin, HistoryMixin, AngeeDataModel):
       @classmethod
       def record_changed_grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
           return (TriggerGrantTarget(ObjectRef("notes/role", "trigger_editor"), "member", "effective_member",
                                      ObjectRef("angee/role", "admin")),)
   ```

   The trigger row (`110_workflows.trigger.yaml`) names `workflow: example.notes.note_publish`, `source: record_changed`,
   `model_label` and a Hasura-style `condition`; it installs disabled (`TriggerResource`, `resources.py`) and an eligible
   user enables it in the console. **Owner rule:** the source declares grant targets and the model its own scope; enablement
   writes tuples to the workflow principal ([REBAC](../../../../../docs/backend/guidelines.md#rebac); `workflows.E001`, `check_record_changed_models` in `triggers.py`).

8. **Own the root-run lifecycle on the record (optional).** Compose `RunSubject`; implement `admit_run(run)` and `settle_run(run, status)`
   with database work only (`RunSubjectRecord` in `src/angee/workflows/testing/models.py`); `workflows.E002` rejects a partial opt-in (`check_run_subject_models` in `subjects.py`).
   **Owner rule:** the record, not a signal receiver, owns admission and settlement ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts)).

9. **Test through the drivers.** Framework addons: `tests/test_<name>.py` with `angee.workflows.testing` installed
   (`INSTALLED_APPS` in `tests/settings.py`) and the `register_step`, `execution`, `run_factory` fixtures. Consumer addons: a `TransactionTestCase`
   in the addon's `tests/` (`NoteWorkflowStepTests` in `examples/src/example/notes/tests/test_workflow_steps.py`): `load_workflow("example.notes.note_publish",
   actor=admin, allow_non_dev=True)`, `start_run(workflow, actor=, subject=)`, `run_until(run)`, `decide(decision, actor=, chosen=[...])`,
   `run_factory(workflow, actor=).at("review")`, inside `capture_tasks()`. **Owner rule:** the drivers compose production admission
   and transition verbs; nothing fabricates step rows ([source-addon test models](../../../../../docs/checks.md#source-addon-test-models)).

## Do not

- External I/O (HTTP, SMTP, a bridge call) inside a `DATABASE` body: an `IO` step keyed on `ctx.idempotency_key` ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts)).
- An addon-local step table, dotted-path resolver or `import_string` of a step: `ANGEE_WORKFLOW_STEP_CLASSES` plus `resolve_step` (`steps.py`; [Autoconfig](../../../../../docs/composer.md#autoconfig)).
- Business branching on the subject's columns inside `run`: a model or queryset method the step calls ([put behavior on the owning object](../../../../../docs/guidelines.md#put-behavior-on-the-owning-object)).
- `Workflow.objects.create(...)`, a hand-set `published`, or an edited `WorkflowVersion.document`: `install_definition` / `save_draft` / `publish` ([Workflow contracts](../../../../../docs/backend/guidelines.md#workflow-contracts), "`Definition` owns graph policy").
- Catching retries, timeouts or validating settlement values in the body: `Retryable`, `RetryPolicy`, `timeout` and `Step.check` own them (`steps.py`).
- Applying a verdict's actions yourself in `continue_with`: `apply_proposals` already ran in the body transaction (`DecisionStep.run` in `decision_steps.py`).
- Reading a record in a body through `Model.objects` or `system_context`: `ctx.load` / `ctx.subject` under the run actor (`context.py`).
- `enabled: true` on a trigger row or a trigger enabled by code: `TriggerResource` clears activation (`resources.py`); enablement is operator state ([Glossary: Trigger source grant](../../../../../docs/glossary.md#backend)).
- A caveated subject in a grant target's zed: `angee.E024` (`check_rebac_caveats` in `src/angee/base/checks.py`); field-backed conditional relations.

## Verify

Done means: system checks are clean (`check_step_registry`, `workflows.E001`, `workflows.E002`), the resource load
leaves `Workflow.published` set, `run_until` reaches a terminal `WorkflowRun` with the expected outcome and output,
and a decision test answers through `decide` and resumes the step.

- In a lane (jj workspace: no host, database or JS install): the focused pytest module, ruff and mypy on touched files,
  `tests/test_layering.py` — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: the composed-host run of consumer tests (`tests/test_composed_examples.py`), the stack
  resource load, permission sync and console SDL/codegen — [composition and schema](../../../../../docs/checks.md#composition-and-schema).
- Loop: [slice](../../../../../.agents/skills/slice/SKILL.md#workflow). False greens: `run_until` returns a *waiting* run without
  failing, so assert `run.status`; `capture_tasks` proves a send was registered, not that a worker ran (`testing/__init__.py`).

## Pitfalls

- Changing `mode` without `timeout` selects that mode's default; an IO timeout at or above the worker budget is refused at resolve time (`Step.mode`, `resolve_step` in `steps.py`).
- A `Done` with an outcome missing from `outcomes` fails the attempt at the body boundary (`Done.check` in `steps.py`).
- Delivery is at least once; bodies must tolerate repeated execution and a non-idempotent uncertain effect waits for an operator ([README](../../README.md)); a wait deadline must be an aware datetime (`Wait.__post_init__` in `steps.py`).
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
Code: src/angee/workflows/{README.md, steps.py, models.py, managers.py, resources.py, autoconfig.py, pyproject.toml, apps.py, context.py, decision_steps.py, subjects.py, triggers.py, definition.py, maps.py, awaits.py, testing/__init__.py, testing/drivers.py, testing/fixtures.py, testing/models.py}; src/angee/decisions/README.md; examples/src/example/notes/{pyproject.toml, autoconfig.py, steps.py, models.py, resources/demo/100_workflows.workflow.yaml, 110_workflows.trigger.yaml, tests/test_workflow_steps.py}; tests/workflow_steps.py; tests/test_workflows_testing.py; tests/test_composed_examples.py; tests/settings.py.
Mismatches: none found for the cited surfaces; no doc lists the YAML node keys (step/next/input/config/body/results/when) — derived from the notes rows and Definition.
-->
