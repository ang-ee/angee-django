import type { BaseAddon } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";

import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

/** Assert the connect contract shared by every channel bridge addon. */
export function expectValidChannelBridgeAddon(addon: BaseAddon): void {
  expectValidBaseAddon(addon);
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
