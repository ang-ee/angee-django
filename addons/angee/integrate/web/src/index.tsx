import type { BaseAddon, BaseAddonRoute, BaseMenuItem } from "@angee/base";
import {
  FolderGit2,
  GitBranch,
  GitFork,
  Link2,
  Plug,
  Store,
  Webhook,
} from "lucide-react";

import { enIntegrateMessages } from "./i18n";
import { IntegrationsPage } from "./views/IntegrationsPage";
import { RepositoriesPage } from "./views/RepositoriesPage";
import { SourcesPage } from "./views/SourcesPage";
import { VCSIntegrationsPage } from "./views/VCSIntegrationsPage";
import { VendorsPage } from "./views/VendorsPage";
import { WebhooksPage } from "./views/WebhooksPage";

const INTEGRATE_ID = "integrate";

const integrateRoutes: readonly BaseAddonRoute[] = [
  {
    // No `menu:` — the route is referenced by exactly one menu item (the
    // Integrations child), so chrome derivation needs no disambiguation.
    name: "integrate.integrations",
    path: "/integrate",
    shell: "console",
    component: IntegrationsPage,
  },
  {
    // The integration record nests under the list; `IntegrationsPage` reads the
    // `$id` param and swaps to the detail form, so this route carries only the URL.
    name: "integrate.integration",
    path: "/integrate/$id",
    shell: "console",
    parent: "integrate.integrations",
  },
  {
    // Static `/integrate/vendors` outranks the `/integrate/$id` param route.
    name: "integrate.vendors",
    path: "/integrate/vendors",
    shell: "console",
    component: VendorsPage,
  },
  {
    name: "integrate.vendor",
    path: "/integrate/vendors/$id",
    shell: "console",
    parent: "integrate.vendors",
  },
  {
    name: "integrate.webhooks",
    path: "/integrate/webhooks",
    shell: "console",
    component: WebhooksPage,
  },
  {
    name: "integrate.webhook",
    path: "/integrate/webhooks/$id",
    shell: "console",
    parent: "integrate.webhooks",
  },
  {
    // Static `/integrate/vcs` outranks the `/integrate/$id` param route, like
    // vendors/webhooks.
    name: "integrate.vcs",
    path: "/integrate/vcs",
    shell: "console",
    component: VCSIntegrationsPage,
  },
  {
    name: "integrate.vcsIntegration",
    path: "/integrate/vcs/$id",
    shell: "console",
    parent: "integrate.vcs",
  },
  {
    name: "integrate.repositories",
    path: "/integrate/repositories",
    shell: "console",
    component: RepositoriesPage,
  },
  {
    name: "integrate.repository",
    path: "/integrate/repositories/$id",
    shell: "console",
    parent: "integrate.repositories",
  },
  {
    name: "integrate.sources",
    path: "/integrate/sources",
    shell: "console",
    component: SourcesPage,
  },
  {
    name: "integrate.source",
    path: "/integrate/sources/$id",
    shell: "console",
    parent: "integrate.sources",
  },
];

const integrateMenu: readonly BaseMenuItem[] = [
  {
    // Route-less app root: the rail icon inherits its target from the first
    // child (Integrations), avoiding a duplicate route reference.
    id: INTEGRATE_ID,
    label: "Integrations",
    icon: "integrate",
    group: "platform",
    children: [
      { id: "integrate.integrations", label: "Integrations", icon: "integration", route: "integrate.integrations" },
      { id: "integrate.vendors", label: "Vendors", icon: "vendor", route: "integrate.vendors" },
      { id: "integrate.webhooks", label: "Webhooks", icon: "webhook", route: "integrate.webhooks" },
      { id: "integrate.vcs", label: "VCS", icon: "vcs", route: "integrate.vcs" },
      { id: "integrate.repositories", label: "Repositories", icon: "repository", route: "integrate.repositories" },
      { id: "integrate.sources", label: "Sources", icon: "source", route: "integrate.sources" },
    ],
  },
];

const integrate: BaseAddon = {
  id: INTEGRATE_ID,
  routes: integrateRoutes,
  menus: integrateMenu,
  i18n: { integrate: enIntegrateMessages },
  icons: {
    integrate: Plug,
    integration: Link2,
    vendor: Store,
    webhook: Webhook,
    vcs: GitFork,
    repository: FolderGit2,
    source: GitBranch,
  },
};

export default integrate;
