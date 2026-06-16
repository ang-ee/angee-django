import type {
  BaseMenuItem,
  BaseAddonRoute,
} from "@angee/base";
import { defineBaseAddon } from "@angee/base";
import { createElement, type ComponentType, type ReactNode } from "react";

import { OperatorTransportProvider } from "./data/transport";
import { enOperatorBundleForMenu, enOperatorMessages } from "./i18n";
import { OperatorGlyph } from "./OperatorGlyph";
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
import {
  SourcesSection,
} from "./views/sections/SourcesSection";
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
    shell: "console",
    menu: OPERATOR_ID,
    component: operatorSectionRoute(OverviewSection),
  },
  {
    name: "operator.services",
    path: "/operator/services",
    shell: "console",
    component: operatorSectionRoute(ServicesSection),
  },
  {
    name: "operator.workspaces",
    path: "/operator/workspaces",
    shell: "console",
    component: operatorSectionRoute(WorkspacesSection),
  },
  {
    name: "operator.sources",
    path: "/operator/sources",
    shell: "console",
    component: operatorSectionRoute(SourcesSection),
  },
  {
    name: "operator.gitops",
    path: "/operator/gitops",
    shell: "console",
    component: operatorSectionRoute(GitOpsSection),
  },
  {
    name: "operator.operations",
    path: "/operator/operations",
    shell: "console",
    component: operatorSectionRoute(OperationsSection),
  },
  {
    name: "operator.templates",
    path: "/operator/templates",
    shell: "console",
    component: operatorSectionRoute(TemplatesSection),
  },
  {
    name: "operator.secrets",
    path: "/operator/secrets",
    shell: "console",
    component: operatorSectionRoute(SecretsSection),
  },
];

// The framework renders a top-level menu item's `children` as the section
// navigation (a `NavigationMenu` dropdown under "Operator") — so the sections live
// in the chrome's own menu, not a hand-rolled tab bar inside the pages.
const operatorRootMenu: BaseMenuItem = {
  id: OPERATOR_ID,
  label: OPERATOR_TITLE,
  icon: OPERATOR_ID,
  group: "platform",
  // The root item owns Operator's rail target; `menu: OPERATOR_ID` selects this crumb for overview.
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
  icons: { operator: OperatorGlyph },
});

export default operator;
