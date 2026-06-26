import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { AtSign, Building2, Contact, Users } from "lucide-react";

import { DirectoriesPage } from "./DirectoriesPage";
import { HandlesPage } from "./HandlesPage";
import { OrganizationsPage } from "./OrganizationsPage";
import { PeoplePage } from "./PeoplePage";

// Each resource page is a routed ResourceList: a list route + a `$id` detail child the
// list page swaps to inline. `resource` tags the collection route so relation fields
// targeting it can "follow" to this detail page.
const consolePage = (
  name: string,
  path: string,
  component: BaseAddonRoute["component"],
  resource?: string,
): readonly BaseAddonRoute[] => [
  { name, path, layout: "console", component, ...(resource ? { resource } : {}) },
  { name: `${name}.record`, path: `${path}/$id`, layout: "console", parent: name },
];

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
    ...consolePage("parties.people", "/parties/people", PeoplePage, "parties.Person"),
    ...consolePage(
      "parties.organizations",
      "/parties/organizations",
      OrganizationsPage,
      "parties.Organization",
    ),
    ...consolePage("parties.handles", "/parties/handles", HandlesPage, "parties.Handle"),
    ...consolePage("parties.directories", "/parties/directories", DirectoriesPage, "parties.Directory"),
  ],
  menus: partiesMenu,
  icons: { parties: Users, organization: Building2, "address-book": Contact, handle: AtSign },
});

export default parties;
