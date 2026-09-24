# Dashboards

`Dashboard` and `DashboardWidget` resource rows declare installed dashboards.
The dashboards addon validates their queries against the composed console
resource metadata after resource loading and when saving a snapshot.
`@angee/ui` owns the built-in widget renderers.

Installed baselines have no actor owner and receive a shared reader through
`ConditionalSharedReaderMixin`. Dashboard and widget reads use the same REBAC
scope as authored dashboards; the shared reader grants no write access. Load
resources only after `migrate` and `rebac sync`, because reader reconciliation
validates against the persisted permission schema. `angee provision` already
runs those steps in that order. When adopting this policy for existing rows,
sync permissions and reload their declared resources to reconcile readers.

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
