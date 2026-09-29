# Projects

Projects owns projects, milestones, tasks and participants.

## Web declarations

The web package exports the declarations the standard routed pages use:

- `projectListDeclaration`, `useProjectFormDeclaration`, `projectRecordTabsFor`;
- `useTaskListDeclaration`, `useTaskFormDeclaration`, `taskRecordTabsFor`;
- `projectTimelineSpec` and `projectTimelineTab`, for milestone bars and task due-date markers on project lanes.

Mount the List and Form elements directly in `ResourceList`: wrapping a declaration in a component hides its marker from the parser. Routes can apply their own collection presets and `admitContributions` for contributed sections and verbs. Through the exported declaration options they select standard groups, verbs, tabs and a form context line. The standard pages consume these same declarations.

The project form's `current_milestone` status field uses the registered `projects.phase` widget, which reads eligible milestone options and invokes the existing phase verb. The phase is displayed once, and project lifecycle verbs stay in the header. Titles and lead bodies come before secondary groups. Operational fields sit in collapsed Details groups, so native creation defaults remain intact.

The project collection Gantt uses project lanes and milestone bars. Project filters, presets, pagination and selection stay on projects; milestone queries are scoped to the visible project lanes. The record Timeline tab uses its own milestone collection and includes task due-date markers. Task links use `useResourceRecordHref`, with app route discriminators and the canonical route as the fallback.

## Project setup

[`Project.objects.setup_from_task`](models.py) promotes a writable task and completes every setup act in one transaction.
- **Receipt:** its dedicated `ProjectSetupReceipt` identifies the actor, the task and the client key. A replay rechecks task and project write authority and compares the canonical input fingerprint without reapplying setup. Ordinary Project insertion keeps ordinary insert semantics.
- **GraphQL:** the `setup_project` verb accepts `ProjectSetupInput`. Addons extend it through `input_extensions`, including nested milestone and round inputs; Strawberry rejects field collisions and unknown input fields.
- **References:** declared public references resolve at the schema boundary through `InputReference`. Cooperative `apply_setup` hooks consume native rows. Other callers resolve their references with the native actor-scoped queryset before calling the same domain owner.
- **Partial projects:** an existing partial project can be completed by adopting uniquely named milestones and filling missing choices. A failed invocation leaves no new acts and no receipt.
- **State and counts:** `setup_state` reports actor-readable persisted evidence. `overdue_milestone_count(milestone_name: ...)` counts the named unfinished phase past its target on an open project; without a name it uses the current phase. Consumers must name the phase when their queue is phase-specific.

See the [emitted-model contracts](../../../tests/test_project_setup.py).
