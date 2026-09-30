# Dashboards

`Dashboard` and `DashboardWidget` resource rows declare installed dashboards.
The dashboards addon validates their queries against the composed console
resource metadata after resource loading and when saving a snapshot.
`@angee/ui` owns the built-in widget renderers.

Widgets may declare `visibility: {resource: "projects.Task", key: "queue__slug", value:
"incoming"}` to require read access to their listing scope. The key must be a
declared container-scope key whose leaf is a unique, ungated scalar. This policy is independent of the
source query's results: an authorized empty widget remains visible. The store
resolves policies under the effective actor; the surface mounts only permitted
widgets and compacts their layout without deleting hidden declarations.
Source queries continue to enforce their own row permissions. Settled visibility
answers remain in use during refetch, so refresh does not blank the dashboard.
Direct and related keys both require declaration in the resource's
`hasura_container_scope_fields`; a unique scalar alone does not admit a
visibility policy. Dotted input keys are rejected at validation; use the
declared Django path with `__` separators.

An installed definition's `revision` owns its baseline: when it differs from
the saved declaration revision, the current declaration is displayed. The
server supplies `can_edit` from the dashboard create permission even for an absent snapshot; `editable: false` on
the definition removes layout editing and reset controls. A hosted resource
view widget uses `kind: "resourceView"` and
`data: {shape: "resourceView", preset: "addon.view-id"}`. The definition's
code-only `views[preset]` component composes the same standard list declaration
as the full page, receives `onListStateChange`, and forwards the provided
embedded scope and reduced chrome settings. The preset owns the immutable
filter and editable view defaults in both places; the list's server total is
the widget heading count. `options.fullViewRoute` opens the full view, while
`options.hint` and `options.audience` supply the rest of the heading.
Value measures supply a heading count only for `count`; a statistic keeps its
metric in the body without repeating it in the heading.
Duplicating a dashboard copies the layout currently displayed, including a
newer declared baseline, into an independent personal snapshot.

The standard refresh footer belongs to built-in query bindings. Authored
(`shape: none`) panels provide their own refresh controls when needed.

Installed baselines have no actor owner. Their [permission policy](permissions.zed)
derives shared reads through a filtered constant over that column, so bulk owner
changes take effect without tuple reconciliation. Dashboard and widget reads use
the same REBAC scope as authored dashboards; the shared reader grants no write
access. The [runtime migration](runtime_migrations/shared_reader_cleanup.py)
removes the retired wildcard tuples during `migrate`, with no prior permission
sync required. Sync permissions afterwards to activate the new schema; resource
reloads are unnecessary for shared access.

## Declared table columns

Any `data.shape: rows` widget can declare `options.columns` as an ordered list
of `{path, label?}` objects to choose which selected fields to display and how
to name their headings. The built-in table composes the shared resource cell
renderer and the resource's metadata for value presentation.

```yaml
kind: table
kindVersion: 1
data:
  shape: rows
  source:
    resource: workflows.Decision
    fields: [action, workflow_name, step_name, created_at]
    filter: {verdict: {exact: pending}}
    sort: [{field: created_at, direction: DESC}]
    limit: 10
options:
  columns:
    - {path: action, label: Decision}
    - {path: workflow_name, label: Workflow}
    - {path: step_name, label: Step}
    - {path: created_at, label: Requested}
```

This is a widget fragment; its resource row also supplies identity, title and
layout. Paths use the resource field vocabulary in `source.fields`, including
dotted paths. Without declared columns, the table displays readable selected
fields in their declared order, excluding the resource identity.

[validate_dashboard_queries](models.py) owns installed and saved query
validation; [WidgetColumnsSchema](../../../packages/ui/src/dashboard/headless.ts)
and its enclosing snapshot schema own the client declaration contract.

The console Decision resource is scoped to decisions the viewer can act on;
the example uses its context labels and the Decision's own creation time.
Journal references retain independent read authorization; see the
[workflow context owner](../workflows/README.md).
