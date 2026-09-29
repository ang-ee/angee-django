# Projects

[`Project.objects.setup_from_task`](models.py) promotes a writable task and
completes its setup in one transaction. The `setup_project` GraphQL verb accepts
data-only configuration, a `client_creation_key`, and an optional task revision.
It resumes an existing promoted project, adopts milestones by unique template
name, fills missing nullable milestone choices without overwriting prior values,
selects the initial milestone only if none is selected, and clones and
binds a vault only if none is bound. Addons extend the cooperative `apply_setup`
hook and consume their own named inputs before delegating once.

The committed request fingerprint makes response-loss retries safe without
reapplying setup after later edits. A failed invocation rolls back its own work;
earlier partial setup remains available for another attempt. Actor-scoped
`setup_state` and `overdue_milestone_count` projections work on projects and tasks
without per-record queries. See the [emitted-model contracts](../../../tests/test_project_setup.py).

Configuration uses `milestones: [{name, description?, start_date?, target_date?}]`
and `vault_template` (a readable vault public ID). Work adds `team` and milestone
`active_stage`; proposals adds `round`; intake adds `submitter`. The respective
addon READMEs describe those inputs. Unknown keys fail validation. Templates are
request data, while each composed model owns its validation and persistence.
