import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { PARTIES_OVERVIEW_SLOT } from "@angee/parties";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarClock, History, Radar, Share2 } from "lucide-react";

import { enNexusMessages } from "./i18n";
import { NetworkPane } from "./NetworkPane";
import { NexusOverviewContribution } from "./NexusOverviewContribution";
import { TimelinePane } from "./TimelinePane";

// Nexus overlays parties rather than standing beside it: a tie and a cadence are
// facts *about* a party, so they belong under the rail the party already owns. A
// top-level root here would make nexus its own app (the chrome derives the app
// rail from the menu roots), presenting an intelligence layer as a destination
// separate from the people it describes.
const nexusMenu: readonly BaseMenuItem[] = [
  {
    id: "nexus.graph",
    label: "Graph",
    route: "nexus.graph",
    parentId: "parties",
    icon: "network",
  },
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
    {
      name: "nexus.graph",
      path: "/nexus/graph",
      layout: "console",
      component: lazyRouteComponent(() => import("./GraphPage"), "GraphPage"),
    },
    ...resourcePageRoutes("nexus.ties", "/nexus/ties", lazyRouteComponent(() => import("./TiesPage"), "TiesPage"), "nexus.Tie"),
    ...resourcePageRoutes("nexus.cadences", "/nexus/cadences", lazyRouteComponent(() => import("./CadencesPage"), "CadencesPage"), "nexus.Cadence"),
  ],
  menus: nexusMenu,
  icons: { cadence: CalendarClock, network: Share2, radar: Radar, timeline: History },
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
    {
      id: "network",
      sequence: 31,
      label: "Network",
      icon: "network",
      render: (context) => {
        const partyId = context.view.kind === "record" ? context.view.sqid : undefined;
        if (context.route?.canonicalLabel !== "parties.Party" || !partyId) return null;
        return <NetworkPane partyId={partyId} />;
      },
    },
    {
      id: "feed",
      sequence: 30,
      label: "Feed",
      icon: "timeline",
      render: (context) => {
        const circleId = context.view.kind === "record" ? context.view.sqid : undefined;
        if (context.route?.canonicalLabel !== "parties.Circle" || !circleId) return null;
        return <TimelinePane circleId={circleId} />;
      },
    },
  ],
  slots: [
    {
      slot: PARTIES_OVERVIEW_SLOT,
      id: "nexus.relationship-health",
      sequence: 20,
      content: <NexusOverviewContribution />,
    },
  ],
});

export default nexus;
