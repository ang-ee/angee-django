import type {
  BaseAddon,
  BaseAddonRoute,
  BreadcrumbItem,
  ChromeMenuItem,
} from "@angee/base";
import { Boxes } from "lucide-react";
import { createElement, type ComponentType, type ReactNode } from "react";

import { OperatorTransportProvider } from "./data/transport";
import { enOperatorBundleForSections } from "./i18n";
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

export interface OperatorSection {
  id: string;
  path: string;
  label: string;
  icon: string;
  breadcrumbs: readonly BreadcrumbItem[];
  component: ComponentType;
}

function childBreadcrumb(label: string): readonly BreadcrumbItem[] {
  return [{ label: OPERATOR_TITLE, to: OPERATOR_ROOT_PATH }, { label }];
}

export const operatorSections = [
  {
    id: "overview",
    path: OPERATOR_ROOT_PATH,
    label: "Overview",
    icon: "home",
    breadcrumbs: [{ label: OPERATOR_TITLE }],
    component: OverviewSection,
  },
  {
    id: "services",
    path: "/operator/services",
    label: "Services",
    icon: "grid",
    breadcrumbs: childBreadcrumb("Services"),
    component: ServicesSection,
  },
  {
    id: "workspaces",
    path: "/operator/workspaces",
    label: "Workspaces",
    icon: "files",
    breadcrumbs: childBreadcrumb("Workspaces"),
    component: WorkspacesSection,
  },
  {
    id: "sources",
    path: "/operator/sources",
    label: "Sources",
    icon: "share",
    breadcrumbs: childBreadcrumb("Sources"),
    component: SourcesSection,
  },
  {
    id: "gitops",
    path: "/operator/gitops",
    label: "GitOps",
    icon: "activity",
    breadcrumbs: childBreadcrumb("GitOps"),
    component: GitOpsSection,
  },
  {
    id: "operations",
    path: "/operator/operations",
    label: "Operations",
    icon: "list",
    breadcrumbs: childBreadcrumb("Operations"),
    component: OperationsSection,
  },
  {
    id: "templates",
    path: "/operator/templates",
    label: "Templates",
    icon: "columns",
    breadcrumbs: childBreadcrumb("Templates"),
    component: TemplatesSection,
  },
  {
    id: "secrets",
    path: "/operator/secrets",
    label: "Secrets",
    icon: "auth",
    breadcrumbs: childBreadcrumb("Secrets"),
    component: SecretsSection,
  },
] satisfies readonly OperatorSection[];

function sectionRouteName(section: OperatorSection): string {
  return `${OPERATOR_ID}.${section.id}`;
}

function operatorSectionRoute(Section: ComponentType): ComponentType {
  return function OperatorSectionRoute(): ReactNode {
    return createElement(
      OperatorTransportProvider,
      null,
      createElement(Section),
    );
  };
}

const operatorRoutes: readonly BaseAddonRoute[] = operatorSections.map((section) => ({
  name: sectionRouteName(section),
  path: section.path,
  shell: "console",
  title: OPERATOR_TITLE,
  icon: OPERATOR_ID,
  breadcrumbs: section.breadcrumbs,
  component: operatorSectionRoute(section.component),
}));

// The framework renders a top-level menu item's `children` as the section
// navigation (a `NavigationMenu` dropdown under "Operator") — so the sections live
// in the chrome's own menu, not a hand-rolled tab bar inside the pages.
const operatorMenu: readonly ChromeMenuItem[] = [
  {
    id: OPERATOR_ID,
    label: OPERATOR_TITLE,
    icon: OPERATOR_ID,
    group: "platform",
    children: operatorSections.map((section) => ({
      id: sectionRouteName(section),
      label: section.label,
      to: section.path,
      icon: section.icon,
    })),
  },
];

const operator: BaseAddon = {
  id: OPERATOR_ID,
  routes: operatorRoutes,
  menus: operatorMenu,
  i18n: enOperatorBundleForSections(operatorSections),
  icons: { operator: Boxes },
};

export default operator;
