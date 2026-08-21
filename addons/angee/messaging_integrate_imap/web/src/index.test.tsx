import { expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateImap from "./index";

describe("messaging_integrate_imap addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateImap)).not.toThrow();
  });

  test("contributes IMAP-specific connect copy", () => {
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.connect.button"]).toBe("Connect IMAP");
    expect(messagingIntegrateImap.menus?.[0]?.description).toBe(
      "Connect IMAP mailbox channels",
    );
  });
});
