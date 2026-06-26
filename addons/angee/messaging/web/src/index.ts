import {
  defineBaseAddon,
  type BaseAddonRoute,
} from "@angee/app";
import { type BaseMenuItem } from "@angee/ui";
import { Inbox, MessagesSquare } from "lucide-react";

import { MessagesPage } from "./MessagesPage";
import { ThreadsPage } from "./ThreadsPage";

// Each page is a routed ResourceList: a list route + a `$id` detail child the list
// swaps to inline. `resource` tags the collection route so relation fields targeting
// it can follow to this detail page.
const consolePage = (
  name: string,
  path: string,
  component: BaseAddonRoute["component"],
  resource?: string,
): readonly BaseAddonRoute[] => [
  { name, path, layout: "console", component, ...(resource ? { resource } : {}) },
  { name: `${name}.record`, path: `${path}/$id`, layout: "console", parent: name },
];

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
    ...consolePage("messaging.inbox", "/messaging/inbox", MessagesPage, "messaging.Message"),
    ...consolePage("messaging.threads", "/messaging/threads", ThreadsPage, "messaging.Thread"),
  ],
  menus: messagingMenu,
  icons: { inbox: Inbox, threads: MessagesSquare },
});

export default messaging;
