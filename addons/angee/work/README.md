# Work

The [stage statusbar](web/src/stage-statusbar.tsx) reads each stage's model-owned
`on_path` projection. Canceled, duplicate and concealing stages appear as one
side-state chip while the ordinary stages remain in queue order. The task's
reopen verb stays in the form header action bar.

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
idempotent verb. Remove selects the queue's concealing stage and refuses promoted
tasks; it never physically deletes a task, and its optional reason is the history
change reason. Restore returns a removed task to the stage history shows before
its latest removal, or to the queue's default stage when that stage no longer
admits hand entry. Concealment withholds the task and its conversation from
everyone but administrators, so only they restore. Clients label Decline as
Reject where appropriate.
