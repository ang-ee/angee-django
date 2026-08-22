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

    const items = appChooserItemsFromMenuItems(menus, "Settings");

    expect(filterAppChooserItems(items, "imap").map((item) => item.id)).toEqual([
      "messaging",
    ]);
    expect(filterAppChooserItems(items, "mailbox").map((item) => item.id)).toEqual([
      "messaging",
    ]);
  });

  test("replaces platform roots with one Settings tile targeting the first root", () => {
    const items = appChooserItemsFromMenuItems([
      { id: "notes", label: "Notes", to: "/notes" },
      {
        id: "iam",
        label: "Permissions",
        group: "platform",
        children: [{ id: "iam.users", label: "Users", to: "/iam/users" }],
      },
      {
        id: "integrate",
        label: "Integrations",
        group: "platform",
        children: [
          { id: "integrate.providers", label: "Providers", to: "/integrate/providers" },
        ],
      },
    ], "Settings");

    expect(items.map(({ id, to }) => ({ id, to }))).toEqual([
      { id: "notes", to: "/notes" },
      { id: "settings", to: "/iam/users" },
    ]);
    expect(filterAppChooserItems(items, "providers").map((item) => item.id))
      .toEqual(["settings"]);
  });
});
