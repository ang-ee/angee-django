import type { BaseAddon } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";

import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

/** Assert the navigation/connect contract shared by every channel bridge addon. */
export function expectValidChannelBridgeAddon(addon: BaseAddon): void {
  expectValidBaseAddon(addon);
  const menus = addon.menus ?? [];
  if (menus.length !== 1) {
    throw new Error(`Channel bridge "${addon.id}" must contribute one menu item.`);
  }
  const menu = menus[0]!;
  if (
    menu.route !== "messaging.channels"
    || menu.parentId !== "messaging"
    || menu.icon !== "channel"
  ) {
    throw new Error(
      `Channel bridge "${addon.id}" must target the shared messaging channels menu.`,
    );
  }
  const connect = addon.slots?.[0];
  if (
    connect?.slot !== MESSAGING_CHANNEL_TOOLBAR_SLOT
    || connect.id !== `${addon.id}.connect`
  ) {
    throw new Error(
      `Channel bridge "${addon.id}" must lead with its shared channel-toolbar connect action.`,
    );
  }
}
