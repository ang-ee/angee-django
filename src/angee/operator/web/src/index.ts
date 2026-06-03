import type { BaseAddon, ChromeMenuItem } from "@angee/base";
import { Boxes } from "lucide-react";

import { enOperatorBundle } from "./i18n";
import {
  OperatorGitOpsPage,
  OperatorOperationsPage,
  OperatorOverviewPage,
  OperatorSecretsPage,
  OperatorServicesPage,
  OperatorSourcesPage,
  OperatorTemplatesPage,
  OperatorWorkspacesPage,
} from "./views/pages";

// The framework renders a top-level menu item's `children` as the section
// navigation (a `NavigationMenu` dropdown under "Operator") — so the sections live
// in the chrome's own menu, not a hand-rolled tab bar inside the pages.
const operatorMenu: readonly ChromeMenuItem[] = [
  {
    id: "operator",
    label: "Operator",
    icon: "operator",
    group: "platform",
    children: [
      { id: "operator.overview", label: "Overview", to: "/operator", icon: "home" },
      { id: "operator.services", label: "Services", to: "/operator/services", icon: "grid" },
      { id: "operator.workspaces", label: "Workspaces", to: "/operator/workspaces", icon: "files" },
      { id: "operator.sources", label: "Sources", to: "/operator/sources", icon: "share" },
      { id: "operator.gitops", label: "GitOps", to: "/operator/gitops", icon: "activity" },
      { id: "operator.operations", label: "Operations", to: "/operator/operations", icon: "list" },
      { id: "operator.templates", label: "Templates", to: "/operator/templates", icon: "columns" },
      { id: "operator.secrets", label: "Secrets", to: "/operator/secrets", icon: "auth" },
    ],
  },
];

// TODO(G1/G2): add route `roles` + `hasRole` nav filtering once the shared
// auth-roles primitives land. Until then the routes are UI-ungated; the server
// REBAC gate (`operatorConnection` → null for non-admins) is the real boundary, and
// the section panes simply show "not configured".
const operator: BaseAddon = {
  id: "operator",
  routes: [
    {
      name: "operator.overview",
      path: "/operator",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator" }],
      component: OperatorOverviewPage,
    },
    {
      name: "operator.services",
      path: "/operator/services",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Services" }],
      component: OperatorServicesPage,
    },
    {
      name: "operator.workspaces",
      path: "/operator/workspaces",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Workspaces" }],
      component: OperatorWorkspacesPage,
    },
    {
      name: "operator.sources",
      path: "/operator/sources",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Sources" }],
      component: OperatorSourcesPage,
    },
    {
      name: "operator.gitops",
      path: "/operator/gitops",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "GitOps" }],
      component: OperatorGitOpsPage,
    },
    {
      name: "operator.operations",
      path: "/operator/operations",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Operations" }],
      component: OperatorOperationsPage,
    },
    {
      name: "operator.templates",
      path: "/operator/templates",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Templates" }],
      component: OperatorTemplatesPage,
    },
    {
      name: "operator.secrets",
      path: "/operator/secrets",
      shell: "console",
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator", to: "/operator" }, { label: "Secrets" }],
      component: OperatorSecretsPage,
    },
  ],
  menus: operatorMenu,
  i18n: enOperatorBundle,
  icons: { operator: Boxes },
};

export default operator;
