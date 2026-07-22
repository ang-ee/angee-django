import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { PARTIES_OVERVIEW_SLOT } from "@angee/parties";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Inbox, Mail, MessagesSquare, Send } from "lucide-react";

import { enMessagingMessages } from "./i18n";
import { MessagingOverviewContribution } from "./MessagingOverviewContribution";
import { RecordActivityPane } from "./RecordActivityPane";
import { RecordChatterPane } from "./RecordChatterPane";

export { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";
export { CHANNEL_MODEL } from "./documents";
export {
  defineChannelBridgeAddon,
  defineChannelPollBridgeAddon,
  type ChannelBridgeAddonOptions,
  type ChannelPollBridgeAddonOptions,
} from "./channel-bridge-addon";
export {
  ChannelPairingAction,
  PairingDialog,
} from "./PairingDialog";

// The reusable record-thread conversation owner (transcript + composer + mark-read
// + live refetch): the record-chatter pane composes it below, and a discuss room
// composes the same one — no second transcript implementation.
export {
  RecordThreadConversation,
  type RecordThreadConversationProps,
  type RecordThreadConversationChrome,
} from "./RecordThreadConversation";

const messagingMenu: readonly BaseMenuItem[] = [
  {
    id: "messaging",
    label: "Messaging",
    icon: "inbox",
    children: [
      { id: "messaging.inbox", label: "Inbox", route: "messaging.inbox", icon: "inbox" },
      { id: "messaging.threads", label: "Threads", route: "messaging.threads", icon: "threads" },
      { id: "messaging.channels", label: "Channels", route: "messaging.channels", icon: "channel" },
    ],
  },
];

const messaging = defineBaseAddon({
  id: "messaging",
  routes: [
    ...resourcePageRoutes("messaging.inbox", "/messaging/inbox", lazyRouteComponent(() => import("./MessagesPage"), "MessagesPage"), "messaging.Message"),
    ...resourcePageRoutes("messaging.threads", "/messaging/threads", lazyRouteComponent(() => import("./ThreadsPage"), "ThreadsPage"), "messaging.Thread"),
    ...resourcePageRoutes("messaging.channels", "/messaging/channels", lazyRouteComponent(() => import("./ChannelsPage"), "ChannelsPage"), "messaging.Channel"),
  ],
  menus: messagingMenu,
  icons: { inbox: Inbox, threads: MessagesSquare, send: Send, channel: Mail },
  i18n: { messaging: enMessagingMessages },
  chatter: [
    {
      id: "comments",
      sequence: 10,
      label: "Comments",
      icon: "comments",
      render: (context) => <RecordChatterPane context={context} />,
    },
    {
      id: "activity",
      sequence: 20,
      label: "Activity",
      icon: "activity",
      render: (context) => <RecordActivityPane context={context} />,
    },
  ],
  slots: [
    {
      slot: PARTIES_OVERVIEW_SLOT,
      id: "messaging.channel-health",
      sequence: 30,
      content: <MessagingOverviewContribution />,
    },
  ],
});

export default messaging;
