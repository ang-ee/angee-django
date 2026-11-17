# Work

The [stage statusbar](web/src/stage-statusbar.tsx) reads each stage's model-owned
`on_path` projection. Canceled and duplicate stages appear as one
side-state chip while the ordinary stages remain in queue order. The task's
reopen verb stays in the form header action bar.

A queue's stages are its owned lines: `work_queues_save` writes them with the
queue, row order is their position, and the queue form shows them as one ordered
section. Triage and duplicate stages are system rows. Their `locked_fields`
projection locks name and category, so lines may change their tone and order but
cannot rename, recategorize, delete or create one; provisioning and resource loads
still own them.

The [`angee.base` stage primitive](../base/models/stages.py) owns stage scope,
admission, the `stage_entered_at` stamp, the `fold` and `rotting_days` hints and
default provisioning; work contributes its category vocabulary and reservations
through `Stage.validate_transition` and the task lifecycle through the
`project_stage` override.

The queue settings form is one owner, `useQueueFormDeclaration` (and
`QueueSettingsForm` for a product's own route). Its sections are
`work.Queue#sections` children: `work.queue-identity` (with the description),
`work.queue-triage`, `work.queue-cadence`, `work.queue-estimates` and
`work.queue-stages`, the Stages lines. A product narrows them per route, for
example `{ only: ["work.queue-stages"], when: { route: "<its route>" } }`.

The setup contributors consume native project teams and optional milestone
`active_stage` choices through their ordinary sharing and stage-validation owners.

`project_tasks(where: {queue__slug: {_eq: "incoming"}})` is the canonical
container-key filter. It runs inside the task's actor scope and works when a
readable task belongs to an unreadable queue. It grants no queue access: the
`queue` relation remains redacted, and ordinary queue-ID filters retain their
existing read requirement. The declaration is filter-only, so it cannot become
a hidden-container grouping or ordering surface.

`accept_tasks`, `decline_tasks`, `remove_tasks` and `restore_tasks` compose their
single verbs for at most 100 distinct selections, each with its observed revision.
One outer transaction commits eligible rows; each refused row rolls back to a
savepoint and returns its own result. A whole-call error rolls back every row.
Retrying with an old revision reports a conflict, including an otherwise
idempotent verb. Remove uses shared trash and keeps the workflow stage; it refuses
promoted tasks. Trash owns the removal time, actor and reason, and restore clears
that state. Owners, inherited queue writers and administrators manage
the removed task. `removed_tasks(queue, parent)` scopes native trash to the
reader's restore permission; merge redirects stay in history instead of that list.

A triage review pauses on a hold. `snooze(until=None, *, awaiting=None,
expected_revision=)` keeps the task in its triage stage, under the same write and
revision check as accept and decline, and may carry a wake date, an awaited user,
both or neither. `snoozed_at` marks the hold and says since when; `snoozed_until` is
only its optional wake date, and the triage inbox hides held tasks by `snoozed_at`.
`wake(reason=, *, message=)` ends the hold and sends `task_woken`
([events](events.py)) with the reason: `due` when the date passed (the
`work.wake_due_snoozes` job), `reply` when the awaited user posted on the task's
chatter (the message rides along), `manual` when a person resumed the review
(`wake_task`, for example after logging an answer received by phone). A chatter
message from anyone else, the reviewer's own included, wakes nothing, and a hold
that awaits nobody is not woken by chatter. Each wake is a per-row save with its
history and revision. Leaving triage (accept, decline, duplicate) ends a hold
without a wake. Waiting is a fact on the task, never a stage: a "waiting on" badge
or saved view reads `snoozed_awaiting`, and a queue keeps its one triage stage.

Duplicate triage calls the shared Task merge coordinator. The work model only
validates and projects the duplicate stage; each reference owner moves its rows.
A task its `remove` blockers refuse (promoted, or in a reserved stage) refuses
the fold through `merge_blockers` before anything moves.
`merged_into` is the operational identity, while the duplicate stage and dropped
reason remain lifecycle facts.

A queue's triage, board and cycles pages are the record pages of the Triage,
Boards and Cycles hubs, under their paths and anchors, so they render in Work
wherever composition places the queue record. [`QueueSettingsLink`](web/src/queue-settings-link.tsx)
opens that record from their headers; a suite that lifts queues into Settings
keeps their configuration there and their work here.

## Stage identities

Operational stages use the `wst_` public-ID prefix. Their integer primary keys,
foreign keys, and history pointers remain unchanged. Earlier `stg_` Work stage
URLs are intentionally unsupported after this upgrade.
