import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { SwatchBook as BrandKitIcon } from "lucide-react";

import { contrastWarningsWidget } from "./ContrastWarnings";
import { enBrandkitMessages } from "./i18n";
import { BRAND_KIT_MODEL, brandKitForm } from "./views/BrandKitForm";

const BRANDKIT_ID = "brandkit";

const brandkitRoutes: readonly BaseAddonRoute[] = resourcePageRoutes(
  "brandkit.kits",
  "/brand-kits",
  lazyRouteComponent(() => import("./views/BrandKitsPage"), "BrandKitsPage"),
  BRAND_KIT_MODEL,
  { menu: BRANDKIT_ID },
);

const brandkitMenu: MenuDeclarations = {
  // Every signed-in person reads kits; only those who manage them get the Settings entry.
  [BRANDKIT_ID]: {
    label: "Brand kits",
    icon: "brandkit",
    group: "platform",
    route: "brandkit.kits",
    requires: `${BRAND_KIT_MODEL}#create`,
  },
};

/**
 * The `@angee/brandkit` rendered addon: the Brand kits page in Settings for
 * whoever may create kits, the registered kit form, and the
 * `angee.brandkit.contrastWarnings` read widget the backend's computed
 * `contrast_warnings` field declares. Applying a kit to the app's theme is not
 * part of this manifest.
 */
const brandkit = defineBaseAddon({
  id: BRANDKIT_ID,
  routes: brandkitRoutes,
  menus: brandkitMenu,
  i18n: { brandkit: enBrandkitMessages },
  icons: { brandkit: BrandKitIcon },
  forms: { [BRAND_KIT_MODEL]: brandKitForm },
  widgets: { "angee.brandkit.contrastWarnings": contrastWarningsWidget },
});

export default brandkit;
