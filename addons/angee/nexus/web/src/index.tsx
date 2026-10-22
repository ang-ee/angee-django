import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { Tab, useRecordChromeContext } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { CalendarClock, Inbox, Radar, Share2 } from "lucide-react";

import { enNexusMessages, useNexusT } from "./i18n";
import { NetworkPane } from "./NetworkPane";
import { NexusOverviewContribution } from "./NexusOverviewContribution";
import { FileMessages, useFileMessageCount } from "./inbox/FileMessages";

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
  menus: {
    nexus: {
      label: "Nexus",
      icon: "nexus-inbox",
      // Where a deployment selecting Nexus lands.
      home: "nexus.inbox",
      // Each included app keeps its own group, so its pages stay a level down.
      include: ["messaging", "parties", "spaces", "posts"],
    },
    "nexus.inbox": { parent: "nexus", label: "Inbox", route: "nexus.inbox", icon: "nexus-inbox", sequence: 10 },
    "nexus.graph": { parent: "nexus", label: "Graph", route: "nexus.graph", icon: "network", sequence: 20 },
    "nexus.ties": { parent: "nexus", label: "Ties", route: "nexus.ties", icon: "radar", sequence: 30, hide: true },
    "nexus.cadences": { parent: "nexus", label: "Cadences", route: "nexus.cadences", icon: "cadence", sequence: 40 },
  },
  icons: { cadence: CalendarClock, network: Share2, radar: Radar, "nexus-inbox": Inbox },
  i18n: { nexus: enNexusMessages },
  containers: {
    "storage.File#groups": {
      "nexus.messages": { sequence: 40, content: {
        label: "Message backlinks",
        useCount: useFileMessageCount,
        render: (context, label) => <>{label()}<li><FileMessages context={context} /></li></>,
      } },
    },
    "parties.overview#items": {
      "nexus.relationship-health": { sequence: 20, content: <NexusOverviewContribution /> },
    },
    "timeline#kinds": {
      "nexus.correspondence": {
        content: {
          label: "Correspondence",
          icon: "channel",
          verb: "Open message",
          target: { page: true },
        },
      },
    },
    "parties.Party#sections": {
      "nexus.network": {
        sequence: 31,
        content: <Tab id="network" label={<NetworkLabel />}><NetworkSection /></Tab>,
      },
    },
  },

});

export default nexus;

function NetworkSection() {
  const { recordId } = useRecordChromeContext();
  return recordId ? <NetworkPane partyId={recordId} /> : null;
}

function NetworkLabel() {
  const t = useNexusT();
  return t("network.label");
}
