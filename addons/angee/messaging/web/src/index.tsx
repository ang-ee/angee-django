import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Inbox, MessagesSquare, Send } from "lucide-react";

import { enMessagingMessages } from "./i18n";
import { RecordActivityPane } from "./RecordActivityPane";
import { RecordChatterPane } from "./RecordChatterPane";

const messagingMenu: readonly BaseMenuItem[] = [
  {
    id: "messaging",
    label: "Messaging",
    icon: "inbox",
    children: [
      { id: "messaging.inbox", label: "Inbox", route: "messaging.inbox", icon: "inbox" },
      { id: "messaging.threads", label: "Threads", route: "messaging.threads", icon: "threads" },
    ],
  },
];

const messaging = defineBaseAddon({
  id: "messaging",
  routes: [
    ...resourcePageRoutes("messaging.inbox", "/messaging/inbox", lazyRouteComponent(() => import("./MessagesPage"), "MessagesPage"), "messaging.Message"),
    ...resourcePageRoutes("messaging.threads", "/messaging/threads", lazyRouteComponent(() => import("./ThreadsPage"), "ThreadsPage"), "messaging.Thread"),
  ],
  menus: messagingMenu,
  icons: { inbox: Inbox, threads: MessagesSquare, send: Send },
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
});

export default messaging;
