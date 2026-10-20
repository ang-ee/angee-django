import * as React from "react";
import {
  ActionMenu,
  ActionTrigger,
  firstDashboardSlot,
  parseDashboardSnapshot,
  useActionResultRun,
  useDashboardRegistry,
  useResourceViewUtilityContext,
  type DashboardSummary,
  type DashboardWidgetKind,
  type WidgetSpec,
} from "@angee/ui";
import type { DashboardStore } from "@angee/ui/dashboard/headless";
import { useDashboardsT } from "./i18n";

export function CaptureDashboardAction(): React.ReactElement | null {
  const store = useDashboardRegistry().store;
  if (!store) return null;
  return <CaptureDashboardMenu store={store} />;
}

function CaptureDashboardMenu({ store }: { store: DashboardStore }): React.ReactElement | null {
  const catalogue = store.useCatalogue();
  const t = useDashboardsT();
  const targets = catalogue.summaries.filter(
    (dashboard) => dashboard.target.scope === "personal" && dashboard.available && dashboard.capabilities.canEdit,
  );
  if (targets.length === 0) return null;
  return (
    <ActionMenu label={null} aria-label={t("capture.add")} glyph="layout-dashboard" variant="ghost" size="iconMd">
      {targets.map((dashboard) => (
        <CaptureDestination key={dashboard.id} store={store} dashboard={dashboard} />
      ))}
    </ActionMenu>
  );
}

function CaptureDestination({ store, dashboard }: {
  store: DashboardStore;
  dashboard: DashboardSummary;
}): React.ReactElement {
  const t = useDashboardsT();
  const context = useResourceViewUtilityContext();
  const binding = store.useDashboard(dashboard.target);
  // The shared settle toasts the save's outcome; the menu item only shows its pending state.
  const settle = useActionResultRun({ noResultTitle: t("common.actionFailed") });
  const [pending, setPending] = React.useState(false);
  const add = async () => {
    const state = binding.state;
    if (state.status !== "ready") return;
    setPending(true);
    const snapshot = state.snapshot;
    const kind: DashboardWidgetKind["id"] = "stat";
    const size = { w: 3, h: 2 };
    const id = globalThis.crypto?.randomUUID?.() ?? `widget-${Date.now()}`;
    const widget: WidgetSpec = {
      schemaVersion: 1,
      id,
      kind,
      kindVersion: 1,
      title: t("capture.widgetTitle", { resource: context.resource }),
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
      await settle(async () => {
        await binding.save({
          target: dashboard.target,
          persistedId: state.persistedId,
          expectedRevision: state.revision,
          name: state.name,
          description: state.description,
          snapshot: parseDashboardSnapshot({ ...snapshot, widgets: [...snapshot.widgets, widget] }),
        });
        return { ok: true, message: t("capture.added", { dashboard: dashboard.title }) };
      });
    } finally {
      setPending(false);
    }
  };
  return (
    <ActionTrigger glyph="dashboard" loading={pending}
      disabled={binding.state.status !== "ready"} onClick={() => void add()}>
      {pending ? t("capture.adding") : dashboard.title}
    </ActionTrigger>
  );
}
