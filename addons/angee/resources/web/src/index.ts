import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enResourcesMessages } from "./i18n";

// Resources contributes a "Resources" section into the platform console — the
// import ledger listing. `parentId: "platform"` nests it under the platform app's
// settings sub-nav, so it has no rail glyph of its own.
const resourcesMenu: readonly BaseMenuItem[] = [
  {
    id: "resources",
    parentId: "platform",
    label: "Resources",
    icon: "archive",
    route: "resources.ledger",
  },
];

const resourcesRoutes: readonly BaseAddonRoute[] = [
  {
    name: "resources.ledger",
    path: "/platform/resources",
    resource: "resources.Resource",
    component: lazyRouteComponent(() => import("./views/ResourcesPage"), "ResourcesPage"),
  },
];

const resources = defineBaseAddon({
  id: "resources",
  routes: resourcesRoutes,
  menus: resourcesMenu,
  i18n: { resources: enResourcesMessages },
});

export default resources;
