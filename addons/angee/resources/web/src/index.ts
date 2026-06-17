import {
  defineBaseAddon,
  type BaseAddonRoute,
  type BaseMenuItem,
} from "@angee/base";

import { ResourcesPage } from "./views/ResourcesPage";

// Resources contributes a "Resources" section into the platform console — the
// import ledger listing. `parentId: "platform"` nests it under the platform app's
// settings sub-nav, so it has no rail glyph of its own.
const resourcesMenu: readonly BaseMenuItem[] = [
  {
    id: "resources",
    parentId: "platform",
    label: "Resources",
    route: "resources.ledger",
  },
];

const resourcesRoutes: readonly BaseAddonRoute[] = [
  {
    name: "resources.ledger",
    path: "/platform/resources",
    shell: "console",
    component: ResourcesPage,
  },
];

const resources = defineBaseAddon({
  id: "resources",
  routes: resourcesRoutes,
  menus: resourcesMenu,
});

export default resources;
