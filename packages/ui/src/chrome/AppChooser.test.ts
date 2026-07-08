import { describe, expect, test } from "vitest";

import {
  appChooserItemsFromMenuItems,
  filterAppChooserItems,
} from "./AppChooser";
import type { ChromeMenuItem } from "./menu-tree";

describe("AppChooser", () => {
  test("searches descendant menu labels and descriptions through the root app", () => {
    const menus: readonly ChromeMenuItem[] = [
      {
        id: "messaging",
        label: "Messaging",
        icon: "inbox",
        children: [
          {
            id: "messaging.inbox",
            label: "Inbox",
            to: "/messaging/inbox",
          },
          {
            id: "messaging.imap",
            label: "IMAP",
            description: "Connect mailbox channels",
            to: "/messaging/channels",
          },
        ],
      },
    ];

    const items = appChooserItemsFromMenuItems(menus);

    expect(filterAppChooserItems(items, "imap").map((item) => item.id)).toEqual([
      "messaging",
    ]);
    expect(filterAppChooserItems(items, "mailbox").map((item) => item.id)).toEqual([
      "messaging",
    ]);
  });
});
