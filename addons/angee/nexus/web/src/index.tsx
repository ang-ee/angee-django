import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarClock, History, Inbox, Radar, Share2 } from "lucide-react";

import { enNexusMessages } from "./i18n";
import { NetworkPane } from "./NetworkPane";
import { NexusOverviewContribution } from "./NexusOverviewContribution";
import { TimelinePane } from "./TimelinePane";

// The personal explorer is a Nexus destination. Relationship analytics retain
// their established placement alongside the Parties records they describe.
const nexusMenu: readonly BaseMenuItem[] = [
  { id: "nexus", label: "Nexus", route: "nexus.inbox", icon: "nexus-inbox" },
  { id: "nexus.inbox", label: "Inbox", route: "nexus.inbox", parentId: "nexus", icon: "nexus-inbox" },
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
    { name: "nexus.inbox", path: "/nexus/inbox", layout: "console", component: lazyRouteComponent(() => import("./InboxPage"), "InboxPage") },
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
  icons: { cadence: CalendarClock, network: Share2, radar: Radar, timeline: History, "nexus-inbox": Inbox },
  i18n: { nexus: enNexusMessages },
  // The cross-channel timeline rides the record chatter seam; the shell applies
  // each canonical model and record predicate before rendering the contribution.
  containers: {
    "parties.overview#items": {
      "nexus.relationship-health": { sequence: 20, content: <NexusOverviewContribution /> },
    },
    "parties.Party#aside": {
      "nexus.timeline": {
        sequence: 30,
        content: {
          label: "Timeline",
          icon: "timeline",
          aliases: ["timeline"],
          when: (context) => context.view.kind === "record",
          render: (context) => <TimelinePane partyId={context.view.sqid ?? ""} />,
        },
      },
      "nexus.network": {
        sequence: 31,
        content: {
          label: "Network",
          icon: "network",
          aliases: ["network"],
          when: (context) => context.view.kind === "record",
          render: (context) => <NetworkPane partyId={context.view.sqid ?? ""} />,
        },
      },
    },
    "parties.Circle#aside": {
      "nexus.feed": {
        sequence: 32,
        content: {
          label: "Feed",
          icon: "timeline",
          aliases: ["feed"],
          when: (context) => context.view.kind === "record",
          render: (context) => <TimelinePane circleId={context.view.sqid ?? ""} />,
        },
      },
    },
  },

});

export default nexus;
