import type { BaseMenuItem } from "@angee/ui";
import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { createElement } from "react";
import { Server } from "lucide-react";

import {
  createOperatorDataProvider,
  OPERATOR_PROVIDER,
} from "./data/operator-provider";
import { OperatorTransportProvider } from "./data/transport";
import {
  enOperatorBundleForMenu,
  enOperatorMessages,
  operatorLogsDrawerTitle,
} from "./i18n";
import { OperatorLogsGlyph } from "./OperatorGlyph";
import { OperatorLogsDrawer } from "./views/sections/LogsDrawer";

const OPERATOR_ID = "operator";
const OPERATOR_TITLE = "Operator";
const OPERATOR_ROOT_PATH = "/operator";

const operatorRoutes: readonly BaseAddonRoute[] = [
  {
    name: "operator.overview",
    path: OPERATOR_ROOT_PATH,
    menu: OPERATOR_ID,
    component: lazyRouteComponent(() => import("./views/sections/OverviewPage"), "OverviewPage"),
  },
  ...resourcePageRoutes("operator.services", "/operator/services", lazyRouteComponent(() => import("./views/sections/ServicesPage"), "ServicesPage"), undefined, {
    detailName: "operator.services.detail",
    detailMenu: "operator.services",
    param: "name",
    detailComponent: lazyRouteComponent(() => import("./views/sections/ServiceDetail"), "ServiceDetail"),
  }),
  ...resourcePageRoutes("operator.workspaces", "/operator/workspaces", lazyRouteComponent(() => import("./views/sections/WorkspacesPage"), "WorkspacesPage"), undefined, {
    detailName: "operator.workspaces.detail",
    detailMenu: "operator.workspaces",
    param: "name",
    detailComponent: lazyRouteComponent(() => import("./views/sections/WorkspaceDetail"), "WorkspaceDetail"),
  }),
  ...resourcePageRoutes("operator.sources", "/operator/sources", lazyRouteComponent(() => import("./views/sections/SourcesPage"), "SourcesPage"), undefined, {
    detailName: "operator.sources.detail",
    detailMenu: "operator.sources",
    param: "name",
    detailComponent: lazyRouteComponent(() => import("./views/sections/SourceDetail"), "SourceDetail"),
  }),
  {
    name: "operator.gitops",
    path: "/operator/gitops",
    component: lazyRouteComponent(() => import("./views/sections/GitOpsPage"), "GitOpsPage"),
  },
  {
    name: "operator.operations",
    path: "/operator/operations",
    component: lazyRouteComponent(() => import("./views/sections/OperationsPage"), "OperationsPage"),
  },
  {
    name: "operator.templates",
    path: "/operator/templates",
    component: lazyRouteComponent(() => import("./views/sections/TemplatesPage"), "TemplatesPage"),
  },
  {
    name: "operator.secrets",
    path: "/operator/secrets",
    component: lazyRouteComponent(() => import("./views/sections/SecretsPage"), "SecretsPage"),
  },
];

// Operator owns its Settings category. It keeps `route: "operator.overview"`
// as its target so the route's `menu: OPERATOR_ID` crumb still resolves to it.
const operatorRootMenu: BaseMenuItem = {
  id: OPERATOR_ID,
  label: OPERATOR_TITLE,
  icon: "terminal",
  group: "platform",
  route: "operator.overview",
  children: [
    {
      label: "Overview",
      route: "operator.overview",
      icon: "home",
    },
    {
      label: "Services",
      route: "operator.services",
      icon: "grid",
    },
    {
      label: "Workspaces",
      route: "operator.workspaces",
      icon: "files",
    },
    {
      label: "Sources",
      route: "operator.sources",
      icon: "share",
    },
    {
      label: "GitOps",
      route: "operator.gitops",
      icon: "activity",
    },
    {
      label: "Operations",
      route: "operator.operations",
      icon: "list",
    },
    {
      label: "Templates",
      route: "operator.templates",
      icon: "columns",
    },
    {
      label: "Secrets",
      route: "operator.secrets",
      icon: "auth",
    },
  ],
};

const operatorMenu: readonly BaseMenuItem[] = [operatorRootMenu];

const operator = defineBaseAddon({
  id: OPERATOR_ID,
  layoutProviders: [{
    id: "operator.transport",
    layout: "console",
    component: OperatorTransportProvider,
    sequence: 10,
  }],
  routes: operatorRoutes,
  menus: operatorMenu,
  i18n: {
    operator: {
      ...enOperatorBundleForMenu(operatorRootMenu).operator,
      ...enOperatorMessages,
    },
  },
  icons: {
    "operator-logs": OperatorLogsGlyph,
    server: Server,
  },
  // The first console-shell drawer adopter: a non-modal bottom drawer streaming
  // a chosen service/workspace's logs. Sticky across navigation (mounted once
  // above the router outlet) and not route-scoped — it picks its own target.
  containers: {
    "shell#drawers-bottom": {
      "operator.logs": {
        sequence: 10,
        content: { title: operatorLogsDrawerTitle, icon: "operator-logs", render: () => createElement(OperatorLogsDrawer) },
      },
    },
  },
  // The daemon's GraphQL surface as a refine data provider, authed by the live
  // bearer the token gate mints. `createApp` registers it alongside the
  // schema-named providers, so panes read/write it via `dataProviderName`.
  dataProviders: { [OPERATOR_PROVIDER]: createOperatorDataProvider() },
});

export default operator;
