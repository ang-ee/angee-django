# Projects

Projects owns projects, milestones, tasks and participants. Its web package
exports the declarations used by the standard routed pages:

- `projectListDeclaration`, `useProjectFormDeclaration`, `projectRecordTabs`.
- `useTaskListDeclaration`, `useTaskFormDeclaration`, `taskRecordTabs`.
- `projectTimelineSpec` and `projectTimelineTab` for milestone bars and task
  due-date markers on project lanes.

Mount the List and Form elements directly in `ResourceList`; wrapping a declaration
in a component hides its marker from the parser. Routes can apply their own
collection presets and `admitContributions` for contributed sections and verbs,
and choose or reorder the exported record tabs. The standard pages consume these
same declarations.

The project form's statusbar is `ProjectPhaseControl`, which reads server-eligible
milestones and invokes the existing phase verb. The phase is displayed once;
project lifecycle verbs remain in the header. Titles and lead bodies precede
secondary groups, with operational fields in collapsed Details groups so native
creation defaults remain intact.

The Timeline tab scopes both sources to the saved project's lane, including an
empty lane. To compose a collection timeline, mount `List`/`ListView` over
`MILESTONE_MODEL` with `laneSource={{ field: "project" }}`, the exported
`projectTimelineSpec`, and `defaultView="gantt"`. The UI collection owner handles
paging, filters, current-milestone emphasis, today indication and read-only
task markers; projects contributes only the source declaration.
