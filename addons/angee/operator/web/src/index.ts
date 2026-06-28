import type { BaseMenuItem } from "@angee/ui";
import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
import { createElement, type ComponentType, type ReactNode } from "react";

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
import {
  GitOpsSection,
} from "./views/sections/GitOpsSection";
import {
  OperationsSection,
} from "./views/sections/OperationsSection";
import {
  OverviewSection,
} from "./views/sections/OverviewSection";
import {
  SecretsSection,
} from "./views/sections/SecretsSection";
import {
  ServicesSection,
} from "./views/sections/ServicesSection";
import { ServiceDetail } from "./views/sections/ServiceDetail";
import {
  SourcesSection,
} from "./views/sections/SourcesSection";
import { SourceDetail } from "./views/sections/SourceDetail";
import { WorkspaceDetail } from "./views/sections/WorkspaceDetail";
import {
  TemplatesSection,
} from "./views/sections/TemplatesSection";
import {
  WorkspacesSection,
} from "./views/sections/WorkspacesSection";

const OPERATOR_ID = "operator";
const OPERATOR_TITLE = "Operator";
const OPERATOR_ROOT_PATH = "/operator";

function operatorSectionRoute(Section: ComponentType): ComponentType {
  return function OperatorSectionRoute(): ReactNode {
    return createElement(
      OperatorTransportProvider,
      null,
      createElement(Section),
    );
  };
}

const operatorRoutes: readonly BaseAddonRoute[] = [
  {
    name: "operator.overview",
    path: OPERATOR_ROOT_PATH,
    layout: "console",
    menu: OPERATOR_ID,
    component: operatorSectionRoute(OverviewSection),
  },
  {
    name: "operator.services",
    path: "/operator/services",
    layout: "console",
    component: operatorSectionRoute(ServicesSection),
  },
  {
    name: "operator.services.detail",
    path: "/operator/services/$name",
    layout: "console",
    menu: "operator.services",
    component: operatorSectionRoute(ServiceDetail),
  },
  {
    name: "operator.workspaces",
    path: "/operator/workspaces",
    layout: "console",
    component: operatorSectionRoute(WorkspacesSection),
  },
  {
    name: "operator.workspaces.detail",
    path: "/operator/workspaces/$name",
    layout: "console",
    menu: "operator.workspaces",
    component: operatorSectionRoute(WorkspaceDetail),
  },
  {
    name: "operator.sources",
    path: "/operator/sources",
    layout: "console",
    component: operatorSectionRoute(SourcesSection),
  },
  {
    name: "operator.sources.detail",
    path: "/operator/sources/$name",
    layout: "console",
    menu: "operator.sources",
    component: operatorSectionRoute(SourceDetail),
  },
  {
    name: "operator.gitops",
    path: "/operator/gitops",
    layout: "console",
    component: operatorSectionRoute(GitOpsSection),
  },
  {
    name: "operator.operations",
    path: "/operator/operations",
    layout: "console",
    component: operatorSectionRoute(OperationsSection),
  },
  {
    name: "operator.templates",
    path: "/operator/templates",
    layout: "console",
    component: operatorSectionRoute(TemplatesSection),
  },
  {
    name: "operator.secrets",
    path: "/operator/secrets",
    layout: "console",
    component: operatorSectionRoute(SecretsSection),
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
