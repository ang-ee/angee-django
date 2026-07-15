import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarClock, History, Radar } from "lucide-react";

import { enNexusMessages } from "./i18n";
import { TimelinePane } from "./TimelinePane";

// Nexus overlays parties rather than standing beside it: a tie and a cadence are
// facts *about* a party, so they belong under the rail the party already owns. A
// top-level root here would make nexus its own app (the chrome derives the app
// rail from the menu roots), presenting an intelligence layer as a destination
// separate from the people it describes.
const nexusMenu: readonly BaseMenuItem[] = [
  {
    id: "nexus.ties",
    label: "Ties",
    route: "nexus.ties",
    parentId: "parties",
    icon: "radar",
  },
  {
    id: "nexus.cadences",
    label: "Cadences",
    route: "nexus.cadences",
    parentId: "parties",
    icon: "cadence",
  },
];

const nexus = defineBaseAddon({
  id: "nexus",
  routes: [
    ...resourcePageRoutes("nexus.ties", "/nexus/ties", lazyRouteComponent(() => import("./TiesPage"), "TiesPage"), "nexus.Tie"),
    ...resourcePageRoutes("nexus.cadences", "/nexus/cadences", lazyRouteComponent(() => import("./CadencesPage"), "CadencesPage"), "nexus.Cadence"),
  ],
  menus: nexusMenu,
  icons: { cadence: CalendarClock, radar: Radar, timeline: History },
  i18n: { nexus: enNexusMessages },
  // The cross-channel timeline rides the record chatter seam and self-gates to
  // party records: a null render on any other model drops the tab entirely.
  chatter: [
    {
      id: "timeline",
      sequence: 30,
      label: "Timeline",
      icon: "timeline",
      render: (context) => {
        const partyId = context.view.kind === "record" ? context.view.sqid : undefined;
        if (context.route?.canonicalLabel !== "parties.Party" || !partyId) return null;
        return <TimelinePane partyId={partyId} />;
      },
    },
  ],
});

export default nexus;
