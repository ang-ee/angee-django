import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarClock, History, Inbox, Radar, Share2 } from "lucide-react";

import { enNexusMessages } from "./i18n";
import { NetworkPane } from "./NetworkPane";
import { NexusOverviewContribution } from "./NexusOverviewContribution";
import { TimelinePane } from "./TimelinePane";

const nexus = defineBaseAddon({
  id: "nexus",
  routes: [
    { name: "nexus.inbox", path: "/nexus/inbox", layout: "console", menu: "nexus.inbox", component: lazyRouteComponent(() => import("./InboxPage"), "InboxPage") },
    {
      name: "nexus.graph",
      path: "/nexus/graph",
      layout: "console",
      component: lazyRouteComponent(() => import("./GraphPage"), "GraphPage"),
    },
    ...resourcePageRoutes("nexus.ties", "/nexus/ties", lazyRouteComponent(() => import("./TiesPage"), "TiesPage"), "nexus.Tie"),
    ...resourcePageRoutes("nexus.cadences", "/nexus/cadences", lazyRouteComponent(() => import("./CadencesPage"), "CadencesPage"), "nexus.Cadence"),
  ],
  menus: {
    nexus: {
      label: "Nexus",
      route: "nexus.inbox",
      icon: "nexus-inbox",
      // Each included app keeps its own group, so its pages stay a level down.
      include: ["messaging", "parties", "spaces", "posts"],
    },
    "nexus.inbox": { parent: "nexus", label: "Inbox", route: "nexus.inbox", icon: "nexus-inbox", sequence: 10 },
    "nexus.graph": { parent: "nexus", label: "Graph", route: "nexus.graph", icon: "network", sequence: 20 },
    "nexus.ties": { parent: "nexus", label: "Ties", route: "nexus.ties", icon: "radar", sequence: 30 },
    "nexus.cadences": { parent: "nexus", label: "Cadences", route: "nexus.cadences", icon: "cadence", sequence: 40 },
  },
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
