---
name: jobs-tasks
description: "Authors Celery work in an Angee addon: tasks.py with shared_task, enqueue_task on-commit dispatch, the DjangoTask base with provenance attribution, retries and task_time_budget, beat entries in autoconfig, queues, idempotency, tests. Use for 'add a task', 'background job', 'periodic', 'beat'."
---

# Jobs Tasks

Run `angee-design` first. A task is a thin entrypoint: it names a row and dispatches to a model or manager verb
that owns the state change. Reference examples are `addons/angee/parties/tasks.py` (periodic, retrying, locked),
`addons/angee/work/tasks.py` (periodic with `timestamp`) and `addons/angee/messaging/delivery.py` (on-commit dispatch).

## Use when

- "Run `<verb>` in the background / after commit / on a worker."
- "Add a periodic / hourly / nightly job for `<addon>`."
- "Retry `<external call>` with backoff" or "give this task a time budget."
- "Add a dedicated queue for a long-lived session" (then also `integrate-bridges`).
- Not for workflow steps or agent sessions: `angee.workflows` and `angee.agents` own their own runners.

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/tasks.py` | `@shared_task(name="<label>.<verb>")` wrappers discovered by `autodiscover_tasks` | `addons/angee/parties/tasks.py:12`, `addons/angee/work/tasks.py:19` |
| `<addon>/autoconfig.py` | `CELERY_BEAT_SCHEDULE:append` entries for the addon's own task names | `addons/angee/parties/autoconfig.py:23-28`, `addons/angee/work/autoconfig.py:5-16` |
| `angee.jobs.enqueue` | `enqueue_task`: on-commit send, `queue`, `expires`, `robust` | `addons/angee/jobs/enqueue.py:14` |
| `angee.jobs.celery` | The one `Celery("angee")` app, `ANGEE_CELERY_TASK_CLASS`, autodiscovery | `addons/angee/jobs/celery.py:17-19` |
| `angee.jobs.timeouts` | `task_time_budget`, `task_claim_lease`, the settlement reserve | `addons/angee/jobs/timeouts.py:7,11,18` |
| `angee.jobs.locks` | `LockKey`, `task_lock`, `record_lock_key`, `task_locks_are_cross_process` | `addons/angee/jobs/locks.py:17,187,194,207` |
| `angee.jobs.scheduler` | `DatabaseScheduler`: code-declared schedule written to `PeriodicTask` rows, stale rows pruned | `addons/angee/jobs/scheduler.py:16` |
| `angee.jobs.autoconfig` | Worker defaults: limits 840/900 s, prefetch 1, ignore results, beat scheduler path, broker from namespace | `addons/angee/jobs/autoconfig.py:8,25` |
| `angee.provenance.tasks.Task` | Attribution: a fresh `changeset(activity="system", change_reason="task.<name>")` per run | `addons/angee/provenance/tasks.py:12`, `addons/angee/provenance/autoconfig.py:25` |
| `manage.py celery`, stack template | Django-loaded Celery CLI; `worker --beat` plus one threads-pool queue worker | `addons/angee/jobs/management/commands/celery.py:11`, `templates/stacks/_shared/stack-body.yaml.jinja:427-448` |

## Reuse first

- Celery native: `shared_task(name=..., autoretry_for=, retry_backoff=, retry_kwargs=, expires=, time_limit=)`;
  the app base is `celery.contrib.django.task:DjangoTask` (`addons/angee/jobs/celery.py:17`) — do not subclass it
  per addon.
- `enqueue_task` for every dispatch from Django code (`addons/angee/jobs/enqueue.py:14`); `send_task` directly only
  inside the jobs addon.
- The verb the task calls: a manager or model method under `system_context(reason=...)` that owns the row lock,
  idempotency and state transition (`addons/angee/parties/tasks.py:40-44`, `addons/angee/work/tasks.py:23-25`).
- `task_lock(LockKey(...))` for duplicate-work suppression around external IO (`addons/angee/jobs/locks.py:194`);
  `task_time_budget()` for any bounded executor (`addons/angee/jobs/timeouts.py:11`).

## Steps

1. **Depend on jobs and name the task.** `pyproject.toml` `depends_on` includes `"angee.jobs"` (transitively
   through `angee.integrate` for most capability addons). The task name is `"<label>.<verb>"`; keep it a
   constant when another module enqueues it (`addons/angee/messaging/constants.py` → `DELIVER_MESSAGE_TASK`).
   **Owner rule:** the name is a wire contract; one constant, read by the task, the enqueue call and the beat
   entry ([Naming](../../../../../docs/backend/guidelines.md#naming)).

2. **Write `tasks.py`** (parties; the task is a wrapper over a manager verb):

   ```python
   from celery import shared_task
   from django.apps import apps
   from rebac import system_context
   from angee.jobs.locks import LockKey, task_lock

   @shared_task(name="parties.refresh_handle_suggestions",
                autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
   def refresh_handle_suggestions(timestamp: int | None = None) -> int:
       del timestamp
       with task_lock(LockKey("parties", ("refresh_handle_suggestions",))) as acquired:
           if not acquired:
               return 0
           parties = apps.get_model("parties", "Party").objects
           with system_context(reason="parties.tasks.refresh_handle_suggestions"):
               return int(parties.reconcile_suggestions())
   ```

   Arguments are identifiers (`model_label`, `pk`, ids), never rows. `apps.get_model` keeps the module importable
   before app population. A periodic task accepts `timestamp: int | None = None`
   ([Task pitfalls](references/pitfalls.md) "Celery periodic tasks accept `timestamp`"). **Owner rule:** [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
   "Task locks are advisory, row locks are authoritative":
   the verb owns persistence policy; the task owns only dispatch and the optional advisory lock.

3. **Dispatch with `enqueue_task`** (messaging; abbreviated from `addons/angee/messaging/delivery.py:79-83`):

   ```python
   with system_context(reason="messaging.delivery.queue"), transaction.atomic():
       row = type(message).objects.sudo(reason="messaging.delivery.queue").lock_if_supported().get(pk=message.pk)
       ...  # the manager decides eligibility and marks the row
       enqueue_task(DELIVER_MESSAGE_TASK,
                    kwargs={"model_label": row._meta.label_lower, "pk": row.pk, "external_id": row.external_id},
                    queue=celery_app.conf.task_default_queue, expires=DELIVERY_TASK_EXPIRES, robust=True)
   ```

   The send waits for the enclosing commit and is discarded with a rolled-back savepoint
   (`tests/test_jobs.py:114,130`). If the caller must observe a broker failure → leave `robust=False`
   (`tests/test_jobs.py:144`). If a reconciler re-enqueues each tick → pass `expires=` of one tick
   (`addons/angee/integrate/scheduler.py:32-35`). **Owner rule:** `enqueue_task` is the one commit boundary
   ([stack](../../../../../docs/stack.md#backend), the Celery row); callers never call `send_task` or `.delay()`.

4. **Declare the schedule in `autoconfig.py`** (parties):

   ```python
   SETTINGS = {
       "CELERY_BEAT_SCHEDULE:append": {
           "parties.refresh_handle_suggestions": {"task": "parties.refresh_handle_suggestions", "schedule": 3600.0},
       },
   }
   ```

   `DatabaseScheduler` writes these into `PeriodicTask` rows at beat startup and deletes rows code no longer
   declares (`addons/angee/jobs/scheduler.py:32-41`); rows hold run state and the enabled flag only.
   **Owner rule:** code owns the schedule, the database owns run state; user-authored cadence is a workflow
   Trigger ([Autoconfig](../../../../../docs/composer.md#autoconfig), `addons/angee/jobs/scheduler.py:17-23`).

5. **Budget time, choose the queue.** Worker limits are `CELERY_TASK_SOFT_TIME_LIMIT=840` / `CELERY_TASK_TIME_LIMIT=900`
   (`addons/angee/jobs/autoconfig.py:16-17`). A bounded body stops at `task_time_budget()` and a persisted claim
   fence outlives both limits by `task_claim_lease()` (`addons/angee/jobs/timeouts.py:11,18`; users:
   `addons/angee/integrate/streams.py:1233`, `addons/angee/workflows/runner.py:352`). If the task outlives a tick
   (a live connection) → a dedicated queue name on the owning impl, `time_limit=None` on the task, and the
   three-check wake loop (`addons/angee/integrate/tasks.py:77`, [Task pitfalls](references/pitfalls.md) "A long-lived Celery task needs the three-check wake loop"); the stack
   runs those queues on a threads-pool worker that enforces no limits (`templates/stacks/_shared/stack-body.yaml.jinja:427-434`).
   Everything else stays on the default queue consumed by the shared `worker --beat`. **Owner rule:** the stack
   owns broker and worker topology; the addon owns per-task budgets ([stack](../../../../../docs/stack.md#backend), the Celery row).

6. **Make it idempotent.** A redelivered or duplicated task must converge: the verb locks the row
   (`lock_if_supported`), revalidates its state, and returns without writing when already done
   (`addons/angee/messaging/delivery.py:36-75`). Advisory `task_lock` is process-local on SQLite; derive liveness
   from it only when `task_locks_are_cross_process()` (`addons/angee/jobs/locks.py:207`). **Owner rule:**
   [Put behavior on the owning object](../../../../../docs/guidelines.md#put-behavior-on-the-owning-object): the
   manager owns convergence, the task owns none.

7. **Attribution is inherited.** With provenance installed the app base is `angee.provenance.tasks:Task`
   (`addons/angee/provenance/autoconfig.py:25`), which opens a fresh `changeset(activity="system",
   change_reason="task.<name>", correlation_id=request.id)` around every run (`addons/angee/provenance/tasks.py:15-22`).
   A task body that runs as a logical pull opens its own nested `changeset(fresh=True, activity="pull", ...)`
   (`addons/angee/integrate/session_runner.py:44-45`). **Owner rule:** the task class owns attribution; bodies
   never stamp `created_by`/`changed_by` themselves ([Changeset](../../../../../docs/glossary.md#backend)).

8. **Test the task.** Call the wrapper directly for its dispatch contract and the manager verb for behavior;
   prove dispatch with `monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", ...)` inside
   `transaction.atomic()` (`tests/test_jobs.py:78-110`). For attribution, run eagerly with
   `celery_app.task(name=..., lazy=False, shared=False)` and `.apply(task_id=..., throw=True)`
   (`tests/test_changesets.py:238-262`). The bare host sets `CELERY_BROKER_URL = "memory://"`
   (`tests/settings.py:145`); no worker runs in tests (`angee-tests`).

## Do not

- `send_task`, `.delay()` or `.apply_async()` from addon code: `enqueue_task` owns the commit boundary (`addons/angee/jobs/enqueue.py:14`; [stack](../../../../../docs/stack.md#backend), the Celery row).
- A per-addon `Celery()` app, task base class or `celery.py`: `angee.jobs.celery.app` and `ANGEE_CELERY_TASK_CLASS` are the owners (`addons/angee/jobs/celery.py:17`; [composition owners](../../../../../docs/backend/guidelines.md#composition-owners)).
- Beat entries in `angee.jobs.autoconfig`, a schedule file or hand-made `PeriodicTask` rows: each addon appends its own names and `DatabaseScheduler` prunes the rest (`tests/test_jobs.py:182,197`; [Autoconfig](../../../../../docs/composer.md#autoconfig)).
- Business rules, row locks held across network IO, or state transitions in the task body: the manager verb owns them ([Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts) "Task locks are advisory, row locks are authoritative"; [thin caller check](../../../../../AGENTS.md#architecture-gate)).
- Model instances or querysets as task arguments: identifiers only, resolved through `apps.get_model` (`addons/angee/work/tasks.py:23`; [Django-native rule](../../../../../docs/backend/guidelines.md#django-native-rule)).
- A long-lived task on the default prefork queue: the global limit kills it and it starves the pool (`addons/angee/integrate/impl.py:214-218`; [Task pitfalls](references/pitfalls.md) "A long-lived Celery task needs the three-check wake loop").
- A DB trigger, cron or second scheduler to maintain rows: one beat per stack, embedded in the shared worker ([stack](../../../../../docs/stack.md#backend), the Celery row; `templates/stacks/_shared/stack-body.yaml.jinja:430-446`; [Models, queries, and resources](../../../../../docs/backend/guidelines.md#models-queries-and-resources)).
- `settings.CELERY_*` reads at module import in `tasks.py` or `autoconfig.py`: settings merge runs before apps are ready ([Autoconfig](../../../../../docs/composer.md#autoconfig)).

## Verify

Done means: the task imports under the bare host, its name appears in `celery_app.tasks` after autodiscovery,
the dispatch test proves on-commit send with the expected `queue`/`expires`, the beat entry names the task
(`tests/test_jobs.py:248` shape), and the manager verb's own tests cover convergence.

- In a lane (jj workspace: no host, database or JS install): focused pytest (`tests/test_jobs.py`, the addon's
  task tests), ruff and mypy on touched files, `tests/test_layering.py` —
  [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: the composed host boot, a whole-application restart after a dependency change, and
  the operator-started `manage.py celery worker --beat` log showing beat installing the entry (never start a
  worker by hand) — [composition and schema](../../../../../docs/checks.md#composition-and-schema),
  [Run from the root](../../../../../AGENTS.md#run-from-the-root).
- Loop: `.agents/skills/slice/SKILL.md`. False green: an eager `.apply()` proves attribution, not routing; only
  the stack worker proves the queue and limits ([what CI actually runs](../../../../../docs/checks.md#what-ci-actually-runs)).

## Pitfalls

- A renamed task leaves a `PeriodicTask` row firing a missing name until beat restarts and prunes it (`addons/angee/jobs/scheduler.py:32-41`).
- Postgres advisory locks are connection-scoped; a reconnect drops them under a running task, so a long loop re-checks the lock ([Task pitfalls](references/pitfalls.md) "A long-lived Celery task needs the three-check wake loop", `addons/angee/jobs/locks.py:207`).
- `enqueue_task` copies `kwargs` at call time; a later mutation of the dict does not reach the task (`tests/test_jobs.py:114-126`).
- A static beat tick calls without `timestamp`; a wrapper that requires it breaks under beat ([Task pitfalls](references/pitfalls.md) "Celery periodic tasks accept `timestamp`").
- The shared worker embeds beat and must never be scaled: beat has no leader lock (`templates/stacks/_shared/stack-body.yaml.jinja:430-446`; [stack](../../../../../docs/stack.md#backend), the Celery row).

## Read next

- [Task pitfalls](references/pitfalls.md)
- [Stack: backend](../../../../../docs/stack.md#backend) Celery row, [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
- [Autoconfig](../../../../../docs/composer.md#autoconfig), [Persistence primitives](../../../../../docs/backend/guidelines.md#persistence-primitives)
- Skills: `angee-models` (the verb), `integrate-bridges` (bridge schedulers and live sessions), `angee-runtime`, `angee-tests`, `slice`

## Sources

<!--
Docs: docs/stack.md #backend (Celery, channels, django-environ rows), #stack-serve-mode; docs/backend/guidelines.md sections Django-Native Rule, Composition owners, Integrations and workers, Persistence primitives, Naming (cited by anchor and bullet title); docs/composer.md #autoconfig; docs/checks.md #python-and-composition-contracts, #composition-and-schema, #browser-verification, #what-ci-actually-runs; docs/guidelines.md #put-behavior-on-the-owning-object; AGENTS.md (Run From The Root, Architecture Gate).
Code: addons/angee/jobs/{__init__.py,pyproject.toml,autoconfig.py,celery.py,enqueue.py,locks.py,scheduler.py,timeouts.py,management/commands/celery.py};
addons/angee/provenance/{tasks.py,autoconfig.py:25}; addons/angee/parties/{tasks.py,autoconfig.py}; addons/angee/work/{tasks.py,autoconfig.py}; addons/angee/messaging/{tasks.py,delivery.py:36-90,autoconfig.py:1-35};
addons/angee/workflows/tasks.py; addons/angee/integrate/{tasks.py:1-120,scheduler.py:1-80,queue.py,constants.py,impl.py:206-224,session_runner.py:27-50};
tests/test_jobs.py; tests/test_changesets.py:238-262; tests/settings.py:140-149; templates/stacks/_shared/stack-body.yaml.jinja:425-450.
Doc gaps: addons/angee/jobs has no README.md and no apps.py; the only prose owner is the Celery row of docs/stack.md#backend plus the Integrations-and-workers bullets; queue naming and the threads-pool worker are documented only in the stack template comment.
Mismatches: docs/stack.md#stack-serve-mode still says "Celery beat stays in its one separate worker service" while the Celery row of docs/stack.md#backend and the template embed it in the shared worker (`worker --beat`, templates/stacks/_shared/stack-body.yaml.jinja:440); owner: docs/stack.md.
-->
