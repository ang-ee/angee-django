export { messageFeedRows, messageFeedWindow, messageFeedRevalidation } from "./message-feed";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { PARTIES_OVERVIEW_SLOT } from "@angee/parties";
import { useAuthoredQuery } from "@angee/refine";
import { type BaseMenuItem } from "@angee/ui";
import type { ChatterContribution, ChatterViewContext } from "@angee/ui/runtime";
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
} from "./documents";

export { MESSAGING_CHANNEL_FORM_FIELDS_SLOT, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";
export { CHANNEL_MODEL, LogRecordActivityDocument } from "./documents";
export { PublicWebform, type PublicWebformProps } from "./PublicWebform";
export {
  ActivityAgendaList,
  type ActivityAgendaListProps,
} from "./ActivityAgendaList";
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

export interface MessagingAddonOptions {
  /** Composer shortcut for this app's built-in Comments tab. */
  submitKey?: NonNullable<RecordThreadConversationProps["submitKey"]>;
}

/** The shared Comments tab, configurable without constructing another addon. */
export function recordCommentsContribution({ id = "comments", submitKey = "enter" }: MessagingAddonOptions & { id?: string } = {}): ChatterContribution {
  return {
    id, sequence: 10, label: "Comments", icon: "comments",
    useCount: useRecordCommentsUnread,
    render: (context) => <RecordChatterPane context={context} submitKey={submitKey} />,
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
    ...resourcePageRoutes("messaging.inbox", "/messaging/inbox", lazyRouteComponent(() => import("./MessagesPage"), "MessagesPage"), "messaging.Message"),
    ...resourcePageRoutes("messaging.threads", "/messaging/threads", lazyRouteComponent(() => import("./ThreadsPage"), "ThreadsPage"), "messaging.Thread"),
    ...resourcePageRoutes("messaging.channels", "/messaging/channels", lazyRouteComponent(() => import("./ChannelsPage"), "ChannelsPage"), "messaging.Channel"),
  ],
  menus: messagingMenu,
  icons: { inbox: Inbox, threads: MessagesSquare, send: Send, channel: Mail },
  i18n: { messaging: enMessagingMessages },
  forms: { "messaging.Channel": channelForm, "messaging.Message": messageForm },
  chatter: [
    recordCommentsContribution({ submitKey }),
    {
      id: "activity",
      sequence: 20,
      label: "Activity",
      icon: "activity",
      render: (context) => <RecordActivityPane context={context} />,
    },
    {
      id: "sources",
      sequence: 30,
      label: "Sources",
      icon: "inbox",
      render: (context) => <RecordSourceThreadsPane context={context} />,
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
