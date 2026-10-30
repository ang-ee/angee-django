import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enPlatformMessages } from "./i18n";

// The platform app is a route-less Settings category (`group: "platform"`). The
// addon owns the schema explorer and import-ledger console; other base addons
// contribute sibling categories.
const platformMenu: MenuDeclarations = {
  "platform": { label: "Platform", icon: "terminal", group: "platform" },
  "platform.explore": { label: "Platform", icon: "terminal", parent: "platform" },
  "platform.graph": { label: "Graph", route: "platform.graph", icon: "share", parent: "platform.explore" },
  "platform.models": { label: "Models", route: "platform.models", icon: "grid", parent: "platform.explore" },
  "platform.fields": { label: "Fields", route: "platform.fields", icon: "columns", parent: "platform.explore" },
  "platform.implementations": { label: "Implementations", route: "platform.implementations", icon: "terminal", parent: "platform.explore" },
  "platform.addons": { label: "Addons", route: "platform.addons", icon: "grid", parent: "platform.explore" },
  "platform.resources": { label: "Resources", icon: "archive", route: "resources.ledger", parent: "platform" },
};

const platformRoutes: readonly BaseAddonRoute[] = [
  { name: "platform.graph", path: "/platform", component: lazyRouteComponent(() => import("./views/GraphPage"), "GraphPage") },
  ...resourcePageRoutes("platform.models", "/platform/models", lazyRouteComponent(() => import("./views/ModelsPage"), "ModelsPage"), "platform.Model", {
    detailName: "platform.models.record",
    detailMenu: "platform.models",
    detailComponent: lazyRouteComponent(() => import("./views/ModelDetail"), "ModelDetail"),
  }),
  { name: "platform.fields", path: "/platform/fields", resource: "platform.Field", component: lazyRouteComponent(() => import("./views/FieldsPage"), "FieldsPage") },
  ...resourcePageRoutes("platform.implementations", "/platform/implementations", lazyRouteComponent(() => import("./views/ImplementationsPage"), "ImplementationsPage"), "platform.Implementation", {
    detailMenu: "platform.implementations",
    detailComponent: lazyRouteComponent(() => import("./views/ImplementationDetail"), "ImplementationDetail"),
  }),
  ...resourcePageRoutes("platform.addons", "/platform/addons", lazyRouteComponent(() => import("./views/AddonsPage"), "AddonsPage"), "platform.Addon", {
    detailName: "platform.addons.record",
    detailMenu: "platform.addons",
    detailComponent: lazyRouteComponent(() => import("./views/AddonDetail"), "AddonDetail"),
  }),
  { name: "resources.ledger", path: "/platform/resources", resource: "resources.Resource", component: lazyRouteComponent(() => import("./views/ResourcesPage"), "ResourcesPage") },
];

const platform = defineBaseAddon({
  id: "platform",
  routes: platformRoutes,
  menus: platformMenu,
  i18n: { platform: enPlatformMessages },
  // The reflection enums' tones: lifecycle state and provenance source.
  statusTones: { enabled: "success", removed: "danger", installed: "neutral", local: "neutral", remote: "info" },
  // Verbs on the addon board's toolbar, such as the VCS marketplace's sources.
  containers: { "platform.addons#toolbar": {} },
});

export default platform;

export { PendingAddonChanges, PLATFORM_ADDON_MUTATION_INVALIDATES, PlatformExplorer } from "./documents";
