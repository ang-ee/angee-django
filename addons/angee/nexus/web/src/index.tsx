import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { History, Radar } from "lucide-react";

import { enNexusMessages } from "./i18n";
import { TimelinePane } from "./TimelinePane";

const nexusMenu: readonly BaseMenuItem[] = [
  {
    id: "nexus",
    label: "Connections",
    icon: "radar",
    children: [
      { id: "nexus.ties", label: "Ties", route: "nexus.ties", icon: "radar" },
      { id: "nexus.cadences", label: "Cadences", route: "nexus.cadences" },
    ],
  },
];

const nexus = defineBaseAddon({
  id: "nexus",
  routes: [
    ...resourcePageRoutes("nexus.ties", "/nexus/ties", lazyRouteComponent(() => import("./TiesPage"), "TiesPage"), "nexus.Tie"),
    ...resourcePageRoutes("nexus.cadences", "/nexus/cadences", lazyRouteComponent(() => import("./CadencesPage"), "CadencesPage"), "nexus.Cadence"),
  ],
  menus: nexusMenu,
  icons: { radar: Radar, timeline: History },
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
