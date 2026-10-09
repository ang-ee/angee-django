export { messageFeedRows, messageFeedWindow, messageFeedRevalidation } from "./message-feed";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { useAuthoredQuery } from "@angee/refine";
import type { ChatterTabContent, ChatterViewContext, ContainerChild } from "@angee/ui/runtime";
import { lazyRouteComponent } from "@tanstack/react-router";
import * as React from "react";
import { Inbox, Mail, MessagesSquare, Send } from "lucide-react";

import { enMessagingMessages } from "./i18n";
import { channelForm } from "./ChannelForm";
import { messageForm } from "./MessageForm";
import { MessagingOverviewContribution } from "./MessagingOverviewContribution";
import { RecordActivityPane } from "./RecordActivityPane";
import { RecordChatterPane } from "./RecordChatterPane";
import type { RecordThreadConversationProps } from "./RecordThreadConversation";
import { RecordSourceThreadsPane } from "./RecordSourceThreadsPane";
import {
  RECORD_UNREAD_COUNT_MODELS,
  RecordThreadUnreadCountDocument,
  THREAD_ATTACHMENT_MODEL,
} from "./documents";

export { CHANNEL_MODEL, LogRecordActivityDocument } from "./documents";
export { PublicWebform, type PublicWebformProps } from "./PublicWebform";
export { ActivityAgendaPane } from "./ActivityAgendaPane";
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
  /** Composer shortcut for this app's built-in Comments tab. */
  submitKey?: NonNullable<RecordThreadConversationProps["submitKey"]>;
}

/** The shared Comments tab, for an addon's `record#aside` or `<model>#aside`, configurable without another addon. */
export function recordCommentsTab({ submitKey = "enter", aliases }: MessagingAddonOptions & { aliases?: readonly string[] } = {}): ContainerChild<ChatterTabContent> {
  return {
    sequence: 10,
    content: {
      label: "Comments", icon: "comments",
      ...(aliases ? { aliases } : {}),
      useCount: useRecordCommentsUnread,
      render: (context) => <RecordChatterPane context={context} submitKey={submitKey} />,
    },
  };
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
    // Messaging supplies the real comments and activity in place of the framework's placeholders.
    "record#aside": {
      "chatter.comments": { remove: true },
      "chatter.activity": { remove: true },
      "messaging.comments": recordCommentsTab({ submitKey, aliases: ["comments"] }),
      "messaging.activity": {
        sequence: 20,
        content: { label: "Activity", icon: "activity", aliases: ["activity"], render: (context) => <RecordActivityPane context={context} /> },
      },
      "messaging.sources": {
        sequence: 30,
        content: {
          label: "Sources", icon: "inbox", aliases: ["sources"],
          // Only on types whose schema lets a record retain source conversations.
          when: (context) => context.route?.recordEdges?.includes(THREAD_ATTACHMENT_MODEL) ?? false,
          render: (context) => <RecordSourceThreadsPane context={context} />,
        },
      },
    },
  },

});

const messaging = defineMessagingAddon();

function useRecordCommentsUnread(
  context: ChatterViewContext,
): number | undefined {
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const enabled = Boolean(modelLabel && recordId);
  const variables = React.useMemo(
    () => ({
      modelLabel: modelLabel ?? "",
      recordId: recordId ?? "",
    }),
    [modelLabel, recordId],
  );
  const query = useAuthoredQuery(RecordThreadUnreadCountDocument, variables, {
    enabled,
    models: RECORD_UNREAD_COUNT_MODELS,
  });
  return query.data?.record_thread_unread_count || undefined;
}

export default messaging;
