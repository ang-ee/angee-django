import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { AtSign, Building2, CircleDot, Contact, HeartHandshake, LayoutDashboard, UserCheck, Users } from "lucide-react";
import { enPartiesMessages } from "./i18n";
import { directoryForm } from "./DirectoryForm";
import { organizationForm } from "./OrganizationForm";
import { personForm } from "./PersonForm";
import { partyForm } from "./PartyForm";
import { partyPickerWidget } from "./PartyPicker";

const parties = defineBaseAddon({
  id: "parties",
  routes: [
    {
      name: "parties.overview",
      path: "/parties",
      layout: "console",
      component: lazyRouteComponent(() => import("./OverviewPage"), "OverviewPage"),
    },
    ...resourcePageRoutes("parties.people", "/parties/people", lazyRouteComponent(() => import("./PeoplePage"), "PeoplePage"), "parties.Person"),
    ...resourcePageRoutes(
      "parties.records",
      "/parties/records",
      lazyRouteComponent(() => import("./PartyRecordRedirect"), "PartyRecordRedirect"),
      "parties.Party",
    ),
    ...resourcePageRoutes(
      "parties.organizations",
      "/parties/organizations",
      lazyRouteComponent(() => import("./OrganizationsPage"), "OrganizationsPage"),
      "parties.Organization",
    ),
    ...resourcePageRoutes("parties.circles", "/parties/circles", lazyRouteComponent(() => import("./CirclesPage"), "CirclesPage"), "parties.Circle"),
    ...resourcePageRoutes(
      "parties.relationships",
      "/parties/relationships",
      lazyRouteComponent(() => import("./RelationshipsPage"), "RelationshipsPage"),
      "parties.Relationship",
    ),
    ...resourcePageRoutes("parties.handles", "/parties/handles", lazyRouteComponent(() => import("./HandlesPage"), "HandlesPage"), "parties.Handle"),
    ...resourcePageRoutes(
      "parties.handle-links",
      "/parties/handle-links",
      lazyRouteComponent(() => import("./ReviewPage"), "ReviewPage"),
      "parties.PartyHandle",
      { detailComponent: lazyRouteComponent(() => import("./PartyHandleRedirect"), "PartyHandleRedirect") },
    ),
    {
      name: "parties.review",
      path: "/parties/review",
      layout: "console",
      component: lazyRouteComponent(() => import("./ReviewPage"), "ReviewPage"),
    },
    {
      name: "parties.merge",
      path: "/parties/merge/$left/$right",
      layout: "console",
      // Param-bearing routes must name their chrome parent (the review queue is
      // the merge flow's home per its breadcrumb).
      parent: "parties.review",
      component: lazyRouteComponent(() => import("./MergePage"), "MergePage"),
    },
    ...resourcePageRoutes("parties.directories", "/parties/directories", lazyRouteComponent(() => import("./DirectoriesPage"), "DirectoriesPage"), "parties.Directory"),
  ],
  menus: {
    // The route-less root lands on the first visible child, People.
    parties: { label: "Parties", icon: "parties" },
    "parties.overview": { parent: "parties", label: "Overview", route: "parties.overview", icon: "overview", hide: true },
    "parties.people": { parent: "parties", label: "People", route: "parties.people", icon: "parties", sequence: 10 },
    "parties.organizations": { parent: "parties", label: "Organizations", route: "parties.organizations", icon: "organization", sequence: 20 },
    "parties.circles": { parent: "parties", label: "Circles", route: "parties.circles", icon: "circle", sequence: 30 },
    "parties.relationships": { parent: "parties", label: "Relationships", route: "parties.relationships", icon: "relationship", hide: true },
    "parties.handles": { parent: "parties", label: "Handles", route: "parties.handles", icon: "handle", hide: true },
    "parties.review": { parent: "parties", label: "Review", route: "parties.review", icon: "user-check", sequence: 40 },
    "parties.directories": { parent: "parties", label: "Contact directories", route: "parties.directories", icon: "address-book", group: "platform" },
  },
  icons: {
    parties: Users,
    overview: LayoutDashboard,
    organization: Building2,
    "address-book": Contact,
    handle: AtSign,
    circle: CircleDot,
    relationship: HeartHandshake,
    "user-check": UserCheck,
  },
  i18n: { parties: enPartiesMessages },
  forms: {
    "parties.Directory": directoryForm,
    "parties.Party": partyForm,
    "parties.Organization": organizationForm,
    "parties.Person": personForm,
  },
  widgets: { partyPicker: partyPickerWidget },
  // Relationship-aware addons add Overview items; contact-consuming addons add Person and Organization form fields.
  containers: { "parties.overview#items": {}, "parties.person#fields": {}, "parties.organization#fields": {} },
});
export { senderDisplayName, type SenderIdentity } from "./identity";
export { addressFields, PartyAddresses } from "./PartyAddresses";
export { useOrganizationFields } from "./OrganizationForm";
export {
  PartyContactSummary,
  partyAddressText,
  partyContactValues,
  type PartyContactSummaryProps,
} from "./PartyContactSummary";
export { PartyPicker, partyPickerWidget, type PartyPickerProps } from "./PartyPicker";

export default parties;
