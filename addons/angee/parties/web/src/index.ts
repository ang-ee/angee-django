import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { AtSign, Building2, Contact, Users } from "lucide-react";
import { enPartiesMessages } from "./i18n";

// One rail root ("Parties") whose children are the People and Organizations
// pages. The root is route-less and inherits its target from the first child.
const partiesMenu: readonly BaseMenuItem[] = [
  {
    id: "parties",
    label: "Parties",
    icon: "parties",
    children: [
      { id: "parties.people", label: "People", route: "parties.people", icon: "parties" },
      {
        id: "parties.organizations",
        label: "Organizations",
        route: "parties.organizations",
        icon: "organization",
      },
      { id: "parties.handles", label: "Handles", route: "parties.handles", icon: "handle" },
      {
        id: "parties.directories",
        label: "Directories",
        route: "parties.directories",
        icon: "address-book",
      },
    ],
  },
];

const parties = defineBaseAddon({
  id: "parties",
  routes: [
    ...resourcePageRoutes("parties.people", "/parties/people", lazyRouteComponent(() => import("./PeoplePage"), "PeoplePage"), "parties.Person"),
    ...resourcePageRoutes(
      "parties.organizations",
      "/parties/organizations",
      lazyRouteComponent(() => import("./OrganizationsPage"), "OrganizationsPage"),
      "parties.Organization",
    ),
    ...resourcePageRoutes("parties.handles", "/parties/handles", lazyRouteComponent(() => import("./HandlesPage"), "HandlesPage"), "parties.Handle"),
    ...resourcePageRoutes("parties.directories", "/parties/directories", lazyRouteComponent(() => import("./DirectoriesPage"), "DirectoriesPage"), "parties.Directory"),
  ],
  menus: partiesMenu,
  icons: { parties: Users, organization: Building2, "address-book": Contact, handle: AtSign },
  i18n: { parties: enPartiesMessages },
});

export default parties;
