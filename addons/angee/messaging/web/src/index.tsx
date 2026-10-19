export { messageFeedRows, messageFeedWindow, messageFeedRevalidation } from "./message-feed";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Inbox, Mail, MessagesSquare, Send } from "lucide-react";

import { enMessagingMessages } from "./i18n";
import { channelForm } from "./ChannelForm";
import { messageForm } from "./MessageForm";
import { MessagingOverviewContribution } from "./MessagingOverviewContribution";
import type { RecordThreadConversationProps } from "./RecordThreadConversation";
import { RecordChatterPane } from "./RecordChatterPane";
import { THREAD_CHATTER_EDGE } from "./documents";

export { CHANNEL_MODEL } from "./documents";
export { RecordChatterPane, type RecordChatterPaneProps } from "./RecordChatterPane";
export { PublicWebform, type PublicWebformProps } from "./PublicWebform";
export {
  defineChannelBridgeAddon,
  defineChannelPollBridgeAddon,
  type ChannelBridgeAddonOptions,
  type ChannelPollBridgeAddonOptions,
  type ChannelRecordAction,
} from "./channel-bridge-addon";
export {
  ChannelPairingAction,
  PairingDialog,
} from "./PairingDialog";
export { usePairingConnect } from "./usePairingConnect";
export { useMessagingT, type MessagingT } from "./i18n";
export {
  ConnectChannelAction,
  type ConnectChannelActionProps,
  type ConnectChannelFields,
  type ConnectChannelParseValues,
  type MutationConnectChannelActionProps,
  type PairingConnectChannelActionProps,
} from "./ConnectChannelAction";

// The reusable record-thread conversation owner (transcript + composer + mark-read
// + live refetch): the record-chatter pane composes it below, and a discuss room
// composes the same one — no second transcript implementation.
export {
  RecordThreadConversation,
  type RecordThreadConversationProps,
  type RecordThreadConversationChrome,
} from "./RecordThreadConversation";
export {
  RecordThreadStream,
  type RecordThreadStreamProps,
  type RecordThreadStreamSource,
  type StreamChildItem,
  type StreamCreateAction,
  type StreamSectionHeading,
} from "./RecordThreadStream";
export {
  ThreadTranscript,
  type ThreadTranscriptProps,
  type TranscriptOrder,
} from "./ThreadTranscript";
// A message read like an email: the summary row every message list renders and
// the reader a message's record composes (Messages, Nexus).
export {
  MESSAGE_SUMMARY_FIELDS,
  MessageSummary,
  type MessageSummaryData,
} from "./MessageSummary";
export { MessageReader, type MessageReaderProps } from "./MessageReader";
export type { MessageReaderData } from "./documents";

export interface MessagingAddonOptions {
  /** Composer shortcut for this app's Activity comment source. */
  submitKey?: NonNullable<RecordThreadConversationProps["submitKey"]>;
}

/** Configure messaging's app contribution through the existing addon manifest. */
export const defineMessagingAddon = ({ submitKey = "enter" }: MessagingAddonOptions = {}) => defineBaseAddon({
  id: "messaging",
  routes: [
    {
      name: "messaging.publicWebforms",
      path: "/public/forms",
      layout: "public",
    },
    {
      name: "messaging.publicWebform",
      path: "/public/forms/$slug",
      parent: "messaging.publicWebforms",
      layout: "public",
      component: lazyRouteComponent(() => import("./PublicWebformPage"), "PublicWebformPage"),
    },
    ...resourcePageRoutes("messaging.messages", "/messaging/messages", lazyRouteComponent(() => import("./MessagesPage"), "MessagesPage"), "messaging.Message"),
    ...resourcePageRoutes("messaging.threads", "/messaging/threads", lazyRouteComponent(() => import("./ThreadsPage"), "ThreadsPage"), "messaging.Thread"),
    ...resourcePageRoutes("messaging.channels", "/messaging/channels", lazyRouteComponent(() => import("./ChannelsPage"), "ChannelsPage"), "messaging.Channel"),
  ],
  menus: {
    messaging: { label: "Messaging", icon: "inbox" },
    // A plain message list. "Inbox" names Nexus's explorer.
    "messaging.messages": { parent: "messaging", label: "Messages", route: "messaging.messages", icon: "inbox", sequence: 10 },
    "messaging.threads": { parent: "messaging", label: "Threads", route: "messaging.threads", icon: "threads", sequence: 20 },
    "messaging.channels": { parent: "messaging", label: "Channels", route: "messaging.channels", icon: "channel", group: "platform" },
  },
  icons: { inbox: Inbox, threads: MessagesSquare, send: Send, channel: Mail },
  i18n: { messaging: enMessagingMessages },
  forms: { "messaging.Channel": channelForm, "messaging.Message": messageForm },
  containers: {
    // The Channels list's Connect menu: each vendor's child renders ActionTrigger or
    // ConnectChannelAction, with any dialog it opens inside it so focus returns to the menu.
    "messaging.channels#toolbar": {},
    "parties.overview#items": {
      "messaging.channel-health": { sequence: 30, content: <MessagingOverviewContribution /> },
    },
    // Comments and source conversations on the Timeline; Discussion is the record's thread.
    "timeline#kinds": {
      "messaging.comment": { content: { label: "Comments", icon: "comments", tone: "info", verb: "Open in Discussion", target: { tab: "messaging.discussion", focus: "message" } } },
      "messaging.conversation": { content: { label: "Conversations", icon: "channel", tone: "muted", verb: "Open conversation", target: { page: true } } },
    },
    "record#aside": {
      "messaging.discussion": { sequence: 20, content: {
        label: "Discussion", icon: "comments", panelClassName: "p-0",
        when: (context) => context.route?.recordEdges?.includes(THREAD_CHATTER_EDGE) ?? false,
        render: (context) => <RecordChatterPane context={context} submitKey={submitKey} />,
      } },
    },
  },

});

const messaging = defineMessagingAddon();

export default messaging;
