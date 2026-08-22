import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { PARTIES_OVERVIEW_SLOT } from "@angee/parties";
import { useAuthoredQuery } from "@angee/refine";
import { type BaseMenuItem } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";
import { lazyRouteComponent } from "@tanstack/react-router";
import * as React from "react";
import { Inbox, Mail, MessagesSquare, Send } from "lucide-react";

import { enMessagingMessages } from "./i18n";
import { MessagingOverviewContribution } from "./MessagingOverviewContribution";
import { RecordActivityPane } from "./RecordActivityPane";
import { RecordChatterPane } from "./RecordChatterPane";
import {
  RECORD_UNREAD_COUNT_MODELS,
  RecordThreadUnreadCountDocument,
} from "./documents";

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
export { usePairingConnect } from "./usePairingConnect";
export type { MessagingT } from "./i18n";
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
      useCount: useRecordCommentsUnread,
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
