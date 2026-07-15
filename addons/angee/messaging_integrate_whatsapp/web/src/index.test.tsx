import { expectValidBaseAddon } from "@angee/app/testing";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { describe, expect, test } from "vitest";

import messagingIntegrateWhatsapp from "./index";

describe("messaging_integrate_whatsapp addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateWhatsapp)).not.toThrow();
  });

  test("contributes the WhatsApp connect action to messaging's channel toolbar", () => {
    expect(messagingIntegrateWhatsapp.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-whatsapp.connect",
      sequence: 20,
    });
    expect(messagingIntegrateWhatsapp.i18n?.messaging?.["channel.whatsapp.button"]).toBe(
      "Connect WhatsApp",
    );
  });

  test("contributes a WhatsApp-labelled menu entry under Messaging", () => {
    expect(messagingIntegrateWhatsapp.menus?.[0]).toMatchObject({
      id: "messaging.whatsapp",
      label: "WhatsApp",
      to: "/messaging/channels",
      parentId: "messaging",
      description: "Link WhatsApp accounts by QR code",
    });
  });
});
