# Projects

Projects owns projects, milestones, tasks and participants. Its web package
exports the declarations used by the standard routed pages:

- `projectListDeclaration`, `useProjectFormDeclaration`, `projectRecordTabsFor`.
- `useTaskListDeclaration`, `useTaskFormDeclaration`, `taskRecordTabsFor`.
- `projectTimelineSpec` and `projectTimelineTab` for milestone bars and task
  due-date markers on project lanes.

Mount the List and Form elements directly in `ResourceList`; wrapping a declaration
in a component hides its marker from the parser. Routes can apply their own
collection presets and `admitContributions` for contributed sections and verbs,
and select standard groups, verbs, tabs, and a form context line through the
exported declaration options. The standard pages consume these same declarations.

The project form's `current_milestone` status field uses the registered
`projects.phase` widget, which reads eligible milestone options and invokes the
existing phase verb. The phase is displayed once;
project lifecycle verbs remain in the header. Titles and lead bodies precede
secondary groups, with operational fields in collapsed Details groups so native
creation defaults remain intact.

The project collection Gantt uses project lanes and milestone bars. Project
filters, presets, pagination, and selection remain on projects; milestone queries
are scoped to the visible project lanes. The record Timeline tab uses its own
milestone collection and includes task due-date markers. Task links use
`useResourceRecordHref`, with app route discriminators and the canonical route
as fallback.
