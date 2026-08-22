import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enPlatformMessages } from "./i18n";
import { PlatformGlyph } from "./PlatformGlyph";

// The platform app is a route-less Settings category (`group: "platform"`). The
// addon owns the schema explorer group; Resources contributes its import ledger
// under `parentId: "platform"` while other base addons own sibling categories.
const platformMenu: readonly BaseMenuItem[] = [
  {
    id: "platform",
    label: "Platform",
    icon: "platform",
    group: "platform",
    children: [
      {
        id: "platform.explore",
        label: "Platform",
        icon: "platform",
        children: [
          { id: "platform.graph", label: "Graph", route: "platform.graph", icon: "share" },
          { id: "platform.models", label: "Models", route: "platform.models", icon: "grid" },
          { id: "platform.fields", label: "Fields", route: "platform.fields", icon: "columns" },
          { id: "platform.addons", label: "Addons", route: "platform.addons", icon: "grid" },
        ],
      },
    ],
  },
];

const platformRoutes: readonly BaseAddonRoute[] = [
  { name: "platform.graph", path: "/platform", component: lazyRouteComponent(() => import("./views/GraphPage"), "GraphPage") },
  ...resourcePageRoutes("platform.models", "/platform/models", lazyRouteComponent(() => import("./views/ModelsPage"), "ModelsPage"), "platform.Model", {
    detailName: "platform.models.record",
    detailMenu: "platform.models",
    detailComponent: lazyRouteComponent(() => import("./views/ModelDetail"), "ModelDetail"),
  }),
  { name: "platform.fields", path: "/platform/fields", resource: "platform.Field", component: lazyRouteComponent(() => import("./views/FieldsPage"), "FieldsPage") },
  ...resourcePageRoutes("platform.addons", "/platform/addons", lazyRouteComponent(() => import("./views/AddonsPage"), "AddonsPage"), "platform.Addon", {
    detailName: "platform.addons.record",
    detailMenu: "platform.addons",
    detailComponent: lazyRouteComponent(() => import("./views/AddonDetail"), "AddonDetail"),
  }),
];

const platform = defineBaseAddon({
  id: "platform",
  routes: platformRoutes,
  menus: platformMenu,
  i18n: { platform: enPlatformMessages },
  icons: { platform: PlatformGlyph },
});

export default platform;
