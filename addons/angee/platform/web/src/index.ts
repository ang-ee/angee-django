import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";

import { enPlatformMessages } from "./i18n";
import { PlatformGlyph } from "./PlatformGlyph";
import { AddonDetail } from "./views/AddonDetail";
import { AddonsPage } from "./views/AddonsPage";
import { FieldsPage } from "./views/FieldsPage";
import { GraphPage } from "./views/GraphPage";
import { ModelDetail } from "./views/ModelDetail";
import { ModelsPage } from "./views/ModelsPage";

// The platform app: a route-less rail root in the bottom platform cluster
// (`group: "platform"`) that opts into the left settings sub-nav (`sidebar: true`).
// Its three top-level sections — Platform (the schema explorer), Operator, and
// Resources — render as top-bar dropdowns *and* in the sidebar. The platform addon
// owns the "Platform" group; `operator` and `resources` contribute the other two
// under `parentId: "platform"`.
const platformMenu: readonly BaseMenuItem[] = [
  {
    id: "platform",
    label: "Platform",
    icon: "platform",
    group: "platform",
    sidebar: true,
    children: [
      {
        id: "platform.explore",
        label: "Platform",
        icon: "platform",
        children: [
          { id: "platform.graph", label: "Graph", route: "platform.graph", icon: "share" },
          { id: "platform.models", label: "Models", route: "platform.models", icon: "grid" },
          { id: "platform.fields", label: "Fields", route: "platform.fields", icon: "columns" },
          { id: "platform.addons", label: "Addons", route: "platform.addons", icon: "list" },
        ],
      },
    ],
  },
];

const platformRoutes: readonly BaseAddonRoute[] = [
  { name: "platform.graph", path: "/platform", layout: "console", component: GraphPage },
  { name: "platform.models", path: "/platform/models", layout: "console", resource: "platform.Model", component: ModelsPage },
  { name: "platform.models.record", path: "/platform/models/$id", layout: "console", menu: "platform.models", component: ModelDetail },
  { name: "platform.fields", path: "/platform/fields", layout: "console", resource: "platform.Field", component: FieldsPage },
  { name: "platform.addons", path: "/platform/addons", layout: "console", resource: "platform.Addon", component: AddonsPage },
  { name: "platform.addons.record", path: "/platform/addons/$id", layout: "console", menu: "platform.addons", component: AddonDetail },
];

const platform = defineBaseAddon({
  id: "platform",
  routes: platformRoutes,
  menus: platformMenu,
  i18n: { platform: enPlatformMessages },
  icons: { platform: PlatformGlyph },
});

export default platform;
