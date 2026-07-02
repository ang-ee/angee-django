import type { BaseMenuItem } from "@angee/ui";
import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import { lazyRouteComponent, type RouteComponent } from "@tanstack/react-router";
import { createElement, type ReactNode } from "react";

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
import { OperatorGlyph, OperatorLogsGlyph } from "./OperatorGlyph";
import { OperatorLogsDrawer } from "./views/sections/LogsDrawer";

const OPERATOR_ID = "operator";
const OPERATOR_TITLE = "Operator";
const OPERATOR_ROOT_PATH = "/operator";

// Each routed page is its own chunk: the dynamic import code-splits the view,
// and the wrapper threads the (light, eager) operator transport around it. The
// page's load suspends to the router-owned pending fallback.
function operatorPageRoute(Page: RouteComponent): RouteComponent {
  return function OperatorPageRoute(): ReactNode {
    return createElement(
      OperatorTransportProvider,
      null,
      createElement(Page),
    );
  };
}

const operatorRoutes: readonly BaseAddonRoute[] = [
  {
    name: "operator.overview",
    path: OPERATOR_ROOT_PATH,
    menu: OPERATOR_ID,
    component: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/OverviewPage"), "OverviewPage"),
    ),
  },
  ...resourcePageRoutes("operator.services", "/operator/services", operatorPageRoute(
    lazyRouteComponent(() => import("./views/sections/ServicesPage"), "ServicesPage"),
  ), undefined, {
    detailName: "operator.services.detail",
    detailMenu: "operator.services",
    param: "name",
    detailComponent: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/ServiceDetail"), "ServiceDetail"),
    ),
  }),
  ...resourcePageRoutes("operator.workspaces", "/operator/workspaces", operatorPageRoute(
    lazyRouteComponent(() => import("./views/sections/WorkspacesPage"), "WorkspacesPage"),
  ), undefined, {
    detailName: "operator.workspaces.detail",
    detailMenu: "operator.workspaces",
    param: "name",
    detailComponent: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/WorkspaceDetail"), "WorkspaceDetail"),
    ),
  }),
  ...resourcePageRoutes("operator.sources", "/operator/sources", operatorPageRoute(
    lazyRouteComponent(() => import("./views/sections/SourcesPage"), "SourcesPage"),
  ), undefined, {
    detailName: "operator.sources.detail",
    detailMenu: "operator.sources",
    param: "name",
    detailComponent: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/SourceDetail"), "SourceDetail"),
    ),
  }),
  {
    name: "operator.gitops",
    path: "/operator/gitops",
    component: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/GitOpsPage"), "GitOpsPage"),
    ),
  },
  {
    name: "operator.operations",
    path: "/operator/operations",
    component: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/OperationsPage"), "OperationsPage"),
    ),
  },
  {
    name: "operator.templates",
    path: "/operator/templates",
    component: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/TemplatesPage"), "TemplatesPage"),
    ),
  },
  {
    name: "operator.secrets",
    path: "/operator/secrets",
    component: operatorPageRoute(
      lazyRouteComponent(() => import("./views/sections/SecretsPage"), "SecretsPage"),
    ),
  },
];

// Operator contributes its console into the platform app: `parentId: "platform"`
// nests this group under the platform settings sub-nav, where its `children`
// render as the Operator section list. It keeps `route: "operator.overview"` as
// its target so the route's `menu: OPERATOR_ID` crumb still resolves to it.
const operatorRootMenu: BaseMenuItem = {
  id: OPERATOR_ID,
  parentId: "platform",
  label: OPERATOR_TITLE,
  icon: OPERATOR_ID,
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
  routes: operatorRoutes,
  menus: operatorMenu,
  i18n: {
    operator: {
      ...enOperatorBundleForMenu(operatorRootMenu).operator,
      ...enOperatorMessages,
    },
  },
  icons: { operator: OperatorGlyph, "operator-logs": OperatorLogsGlyph },
  // The first console-shell drawer adopter: a non-modal bottom drawer streaming
  // a chosen service/workspace's logs. Sticky across navigation (mounted once
  // above the router outlet) and not route-scoped — it picks its own target.
  drawers: [
    {
      id: "logs",
      edge: "bottom",
      title: operatorLogsDrawerTitle,
      icon: "operator-logs",
      sequence: 10,
      render: () => createElement(OperatorLogsDrawer),
    },
  ],
  // The daemon's GraphQL surface as a refine data provider, authed by the live
  // bearer the token gate mints. `createApp` registers it alongside the
  // schema-named providers, so panes read/write it via `dataProviderName`.
  dataProviders: { [OPERATOR_PROVIDER]: createOperatorDataProvider() },
});

export default operator;
