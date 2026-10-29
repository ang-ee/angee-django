
import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
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

const operatorMenu: MenuDeclarations = {
  [OPERATOR_ID]: { label: OPERATOR_TITLE, icon: "terminal", group: "platform", route: "operator.overview" },
  "operator.overview": { label: "Overview", route: "operator.overview", icon: "home", parent: OPERATOR_ID },
  "operator.services": { label: "Services", route: "operator.services", icon: "grid", parent: OPERATOR_ID },
  "operator.workspaces": { label: "Workspaces", route: "operator.workspaces", icon: "files", parent: OPERATOR_ID },
  "operator.sources": { label: "Sources", route: "operator.sources", icon: "share", parent: OPERATOR_ID },
  "operator.gitops": { label: "GitOps", route: "operator.gitops", icon: "activity", parent: OPERATOR_ID },
  "operator.operations": { label: "Operations", route: "operator.operations", icon: "list", parent: OPERATOR_ID },
  "operator.templates": { label: "Templates", route: "operator.templates", icon: "columns", parent: OPERATOR_ID },
  "operator.secrets": { label: "Secrets", route: "operator.secrets", icon: "auth", parent: OPERATOR_ID },
};

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
      ...enOperatorBundleForMenu(operatorMenu, OPERATOR_ID).operator,
      ...enOperatorMessages,
    },
  },
  icons: {
    "operator-logs": OperatorLogsGlyph,
    server: Server,
  },
  // The daemon's own status words the shared vocabulary lacks: a service the
  // runtime keeps restarting after it exited needs attention, like a pending one;
  // an application with a service down is unavailable; a working tree is dirty or
  // clean; a branch is pushed or unpushed; a secret is set, empty or protected.
  statusTones: {
    restarting: "warning", unavailable: "warning", dirty: "warning", clean: "success",
    pushed: "success", unpushed: "warning", set: "success", empty: "neutral", protected: "info",
  },
  // The first console-shell drawer adopter: a non-modal bottom drawer streaming
  // a chosen service's logs. Sticky across navigation (mounted once
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
