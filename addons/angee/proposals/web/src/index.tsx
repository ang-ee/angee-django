import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import {
  Eye,
  GitCompareArrows,
  Gavel,
  MessageSquareText,
  Send,
  Share2,
  Undo2,
  UserRoundCog,
} from "lucide-react";

import { enProposalsMessages } from "./i18n";
import { roundRecordSlots } from "./record-rounds";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";

const proposalsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "proposals.rounds",
    "/proposals/rounds",
    lazyRouteComponent(() => import("./views/RoundsPage"), "RoundsPage"),
    ROUND_MODEL,
  ),
  {
    name: "proposals.round-comparison",
    path: "/proposals/rounds/$id/compare",
    parent: "proposals.rounds.record",
    menu: "proposals.rounds",
    component: lazyRouteComponent(
      () => import("./views/RoundComparisonPage"),
      "RoundComparisonPage",
    ),
  },
  ...resourcePageRoutes(
    "proposals.proposals",
    "/proposals/responses",
    lazyRouteComponent(
      () => import("./views/ProposalsPage"),
      "ProposalsPage",
    ),
    PROPOSAL_MODEL,
  ),
];

const proposalsMenu: readonly BaseMenuItem[] = [
  {
    id: "proposals",
    label: "Proposals",
    icon: "proposals-round",
    children: [
      {
        id: "proposals.rounds",
        label: "Rounds",
        icon: "proposals-round",
        route: "proposals.rounds",
      },
      {
        id: "proposals.proposals",
        label: "Responses",
        icon: "proposals-response",
        route: "proposals.proposals",
      },
    ],
  },
];

const proposals = defineBaseAddon({
  id: "proposals",
  routes: proposalsRoutes,
  menus: proposalsMenu,
  i18n: { proposals: enProposalsMessages },
  slots: roundRecordSlots,
  icons: {
    "proposals-round": GitCompareArrows,
    "proposals-response": MessageSquareText,
    "proposals-open": Eye,
    "proposals-award": Gavel,
    "proposals-transfer": UserRoundCog,
    "proposals-submit": Send,
    "proposals-withdraw": Undo2,
    "proposals-publish": Share2,
  },
});

export {
  ANSWER_MODEL,
  PROPOSAL_MODEL,
  REVIEW_MODEL,
  ROUND_MODEL,
  TOPIC_MODEL,
} from "./resources";
export default proposals;
