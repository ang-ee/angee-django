import { expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateImap from "./index";

describe("messaging_integrate_imap addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateImap)).not.toThrow();
  });

  test("contributes the credential re-entry verb to IMAP channel rows only", () => {
    expect(messagingIntegrateImap.slots?.[1]).toMatchObject({
      slot: "form-view.record-actions",
      model: "messaging.Channel",
      impl: "imap",
      id: "messaging-integrate-imap.credential",
      sequence: 20,
    });
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.imap.credential.button"]).toBe("Update credential");
  });

  test("contributes IMAP-specific connect copy", () => {
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.imap.button"]).toBe("Connect IMAP");
    expect(messagingIntegrateImap.menus?.[0]?.description).toBe(
      "Connect IMAP mailbox channels",
    );
  });
});
