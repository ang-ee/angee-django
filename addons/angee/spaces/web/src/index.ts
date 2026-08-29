import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { UsersRound } from "lucide-react";

import { enSpacesMessages } from "./i18n";

const spaces = defineBaseAddon({
  id: "spaces",
  routes: resourcePageRoutes(
    "spaces.groups",
    "/spaces/groups",
    lazyRouteComponent(() => import("./SpacesPage"), "SpacesPage"),
    "spaces.Group",
  ),
  menus: [
    {
      id: "spaces",
      label: "Spaces",
      icon: "spaces",
      route: "spaces.groups",
    },
  ],
  icons: { spaces: UsersRound },
  i18n: { spaces: enSpacesMessages },
});

export default spaces;
