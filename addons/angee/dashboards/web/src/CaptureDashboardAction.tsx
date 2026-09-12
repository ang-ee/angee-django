import * as React from "react";
import {
  Button,
  firstDashboardSlot,
  parseDashboardSnapshot,
  useDashboardRegistry,
  useResourceViewActionContext,
  type DashboardSummary,
  type DashboardWidgetKind,
  type WidgetSpec,
} from "@angee/ui";
import type { DashboardStore } from "@angee/ui/dashboard/headless";

export function CaptureDashboardAction(): React.ReactElement | null {
  const store = useDashboardRegistry().store;
  if (!store) return null;
  return <CaptureDashboardMenu store={store} />;
}

function CaptureDashboardMenu({ store }: { store: DashboardStore }): React.ReactElement | null {
  const catalogue = store.useCatalogue();
  const [open, setOpen] = React.useState(false);
  const targets = catalogue.summaries.filter(
    (dashboard) => dashboard.target.scope === "personal" && dashboard.available && dashboard.capabilities.canEdit,
  );
  if (targets.length === 0) return null;
  return (
    <span className="relative inline-flex">
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen((value) => !value)}>Add to dashboard</Button>
      {open ? (
        <span className="absolute left-0 top-full z-popover mt-1 flex min-w-56 flex-col rounded-8 border border-border bg-sheet p-1 shadow-md">
          {targets.map((dashboard) => (
            <CaptureDestination key={dashboard.id} store={store} dashboard={dashboard} onDone={() => setOpen(false)} />
          ))}
        </span>
      ) : null}
    </span>
  );
}

function CaptureDestination({ store, dashboard, onDone }: {
  store: DashboardStore;
  dashboard: DashboardSummary;
  onDone: () => void;
}): React.ReactElement {
  const context = useResourceViewActionContext();
  const binding = store.useDashboard(dashboard.target);
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);
  const add = async () => {
    if (binding.state.status !== "ready") return;
    setPending(true);
    setError(null);
    const snapshot = binding.state.snapshot;
    const kind: DashboardWidgetKind["id"] = "stat";
    const size = { w: 3, h: 2 };
    const id = globalThis.crypto?.randomUUID?.() ?? `widget-${Date.now()}`;
    const widget: WidgetSpec = {
      schemaVersion: 1,
      id,
      kind,
      kindVersion: 1,
      title: `${context.resource} total`,
      data: {
        shape: "value",
        source: {
          resource: context.resource,
          filter: context.filter,
          measure: { op: "count" },
        },
      },
      options: {},
      ...firstDashboardSlot(snapshot.widgets.filter((item) => !item.isArchived), size, snapshot.columns),
      ...size,
      isArchived: false,
    };
    try {
      await binding.save({
        target: dashboard.target,
        persistedId: binding.state.persistedId,
        expectedRevision: binding.state.revision,
        name: binding.state.name,
        description: binding.state.description,
        snapshot: parseDashboardSnapshot({ ...snapshot, widgets: [...snapshot.widgets, widget] }),
      });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setPending(false);
    }
  };
  return (
    <span className="flex flex-col">
      <button type="button" disabled={pending || binding.state.status !== "ready"} className="rounded-6 px-3 py-2 text-left text-13 text-fg hover:bg-inset disabled:opacity-50" onClick={() => void add()}>
        {pending ? "Adding…" : dashboard.title}
      </button>
      {error ? <span role="alert" className="px-3 pb-2 text-2xs text-danger-text">{error.message}</span> : null}
    </span>
  );
}
