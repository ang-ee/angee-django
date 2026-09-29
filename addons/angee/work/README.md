# Work

The setup contributors configure the project team and resolve optional milestone
`active_stage` choices through their ordinary sharing and stage-validation owners.

`project_tasks(where: {queue__slug: {_eq: "incoming"}})` is the canonical
container-key filter. It runs inside the task's actor scope and works when a
readable task belongs to an unreadable queue. It grants no queue access: the
`queue` relation remains redacted, and ordinary queue-ID filters retain their
existing read requirement. The declaration is filter-only, so it cannot become
a hidden-container grouping or ordering surface.
