# Projects

[`Project.objects.setup_from_task`](models.py) promotes a writable task and
completes all setup acts in one transaction. Its dedicated `ProjectSetupReceipt`
identifies the actor, task and client key. Replay rechecks task and project write
authority and compares the canonical input fingerprint without reapplying setup.
Ordinary Project insertion retains ordinary insert semantics.

The GraphQL `setup_project` verb accepts `ProjectSetupInput`. Addons extend it
through `input_extensions`, including nested milestone and round inputs;
Strawberry rejects field collisions and unknown input fields. Declared public
references resolve at the schema boundary through `InputReference`. Cooperative
`apply_setup` hooks consume native rows. Other callers resolve their references
with the native actor-scoped queryset before calling the same domain owner.

An existing partial project can be completed by adopting uniquely named
milestones and filling missing choices. A failed invocation leaves no new acts
or receipt. `setup_state` reports actor-readable persisted evidence.
`overdue_milestone_count(milestone_name: ...)` counts the specified unfinished
phase past its target on an open project; omitting the name selects the current
phase. Consumers must name the phase when their queue is phase-specific.
See the [emitted-model contracts](../../../tests/test_project_setup.py).
