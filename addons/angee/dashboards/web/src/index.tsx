import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
import {
  DASHBOARD_STORE_SLOT,
  resourceViewActionsSlot,
  type BaseMenuItem,
  type DashboardDefinition,
} from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Gauge, LayoutDashboard } from "lucide-react";

import { CaptureDashboardAction } from "./CaptureDashboardAction";
import { dashboardStore } from "./store";

const DASHBOARDS_MODEL = "dashboards.Dashboard";

const overview: DashboardDefinition = {
  key: "dashboards.overview",
  title: "Dashboard overview",
  revision: "1",
  columns: 12,
  resource: DASHBOARDS_MODEL,
  widgets: [
    {
      schemaVersion: 1,
      id: "dashboard-count",
      kind: "stat",
      kindVersion: 1,
      title: "Visible dashboards",
      data: { shape: "value", source: { resource: DASHBOARDS_MODEL, measure: { op: "count" } } },
      options: {},
      x: 0,
      y: 0,
      w: 3,
      h: 2,
      isArchived: false,
    },
    {
      schemaVersion: 1,
      id: "dashboard-scopes",
      kind: "donut",
      kindVersion: 1,
      title: "By scope",
      data: {
        shape: "series",
        source: { resource: DASHBOARDS_MODEL, groups: [{ field: "scope" }], measure: { op: "count" }, limit: 8 },
      },
      options: {},
      x: 3,
      y: 0,
      w: 4,
      h: 4,
      isArchived: false,
    },
  ],
};

const routes: readonly BaseAddonRoute[] = [
  {
    name: "dashboards.index",
    path: "/dashboards",
    resource: DASHBOARDS_MODEL,
    component: lazyRouteComponent(() => import("./DashboardCataloguePage"), "DashboardCataloguePage"),
  },
  {
    name: "dashboards.diagnostics",
    path: "/dashboards/diagnostics",
    component: lazyRouteComponent(() => import("./DiagnosticsPage"), "DashboardDiagnosticsPage"),
    menu: "dashboards",
  },
  {
    name: "dashboards.resource",
    path: "/dashboards/resource/$key",
    component: lazyRouteComponent(() => import("./DashboardPage"), "ResourceDashboardPage"),
    menu: "dashboards",
  },
  {
    name: "dashboards.addon",
    path: "/dashboards/addon/$key",
    component: lazyRouteComponent(() => import("./DashboardPage"), "AddonDashboardPage"),
    menu: "dashboards",
  },
  {
    name: "dashboards.detail",
    path: "/dashboards/$id",
    component: lazyRouteComponent(() => import("./DashboardPage"), "PersonalDashboardPage"),
    menu: "dashboards",
  },
];

const menus: readonly BaseMenuItem[] = [
  {
    id: "dashboards",
    label: "Dashboards",
    icon: "dashboards",
    route: "dashboards.index",
  },
];

const dashboards = defineBaseAddon({
  id: "dashboards",
  routes,
  menus,
  dashboards: [overview],
  icons: { dashboards: LayoutDashboard, "dashboard-diagnostics": Gauge },
  slots: [
    {
      slot: DASHBOARD_STORE_SLOT,
      id: "dashboards.store",
      content: dashboardStore,
    },
    {
      ...resourceViewActionsSlot(),
      id: "dashboards.capture",
      sequence: 80,
      content: <CaptureDashboardAction />,
    },
  ],
});

export { dashboardStore } from "./store";
export default dashboards;
