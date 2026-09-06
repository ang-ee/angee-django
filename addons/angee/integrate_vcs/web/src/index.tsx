import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import type { BaseMenuItem } from "@angee/ui";
import { FolderGit2, GitBranch, GitFork, LayoutTemplate } from "lucide-react";

import { enIntegrateVcsMessages } from "./i18n";
import { vcsBridgeForm } from "./views/VcsBridgesPage";

const routes = [
  ...resourcePageRoutes("integrate_vcs.vcs", "/integrate/vcs", lazyRouteComponent(() => import("./views/VcsBridgesPage"), "VcsBridgesPage"), "integrate_vcs.VcsBridge", { detailName: "integrate_vcs.vcsBridge" }),
  ...resourcePageRoutes("integrate_vcs.repositories", "/integrate/repositories", lazyRouteComponent(() => import("./views/RepositoriesPage"), "RepositoriesPage"), "integrate_vcs.Repository", { detailName: "integrate_vcs.repository" }),
  ...resourcePageRoutes("integrate_vcs.sources", "/integrate/sources", lazyRouteComponent(() => import("./views/SourcesPage"), "SourcesPage"), "integrate_vcs.Source", { detailName: "integrate_vcs.source" }),
  ...resourcePageRoutes("integrate_vcs.templates", "/integrate/templates", lazyRouteComponent(() => import("./views/TemplatesPage"), "TemplatesPage"), "integrate_vcs.Template", { detailName: "integrate_vcs.template" }),
] as const;

const menus: readonly BaseMenuItem[] = [{
  id: "integrate_vcs",
  label: "Sources",
  icon: "source",
  group: "platform",
  children: [
    { id: "integrate_vcs.sources", label: "Sources", icon: "source", route: "integrate_vcs.sources" },
    { id: "integrate_vcs.templates", label: "Templates", icon: "integrate-template", route: "integrate_vcs.templates" },
    { id: "integrate_vcs.repositories", label: "Repositories", icon: "repository", route: "integrate_vcs.repositories" },
    { id: "integrate_vcs.vcs", label: "VCS Bridges", icon: "vcs", route: "integrate_vcs.vcs" },
  ],
}];

const integrateVcs = defineBaseAddon({
  id: "integrate_vcs",
  routes,
  menus,
  i18n: { integrate_vcs: enIntegrateVcsMessages },
  forms: { "integrate_vcs.VcsBridge": vcsBridgeForm },
  icons: { vcs: GitFork, repository: FolderGit2, source: GitBranch, "integrate-template": LayoutTemplate },
});

export { RepositoryPicker, type RepositoryPickerProps } from "./views/RepositoryPicker";
export type { RepoCandidate } from "./documents";
export { VCS_BRIDGE_MODEL, VCS_BRIDGE_RELATION } from "./data/vcs-bridge";
export default integrateVcs;
