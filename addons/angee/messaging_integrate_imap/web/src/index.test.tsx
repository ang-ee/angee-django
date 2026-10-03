import { expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateImap from "./index";

/** The vendor's children of the channel form's overflow-menu container. */
const channelMenu = () => messagingIntegrateImap.containers?.["messaging.Channel#actions-menu"];

describe("messaging_integrate_imap addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateImap)).not.toThrow();
  });

  test("contributes the credential re-entry verb to IMAP channel rows only", () => {
    expect(channelMenu()).toMatchObject({
      "messaging-integrate-imap.credential": { impl: "imap", sequence: 20 },
    });
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.imap.credential.button"]).toBe("Update credential");
  });

  test("contributes the paused-channel new-mail boundary action", () => {
    expect(channelMenu()).toMatchObject({
      "messaging-integrate-imap.new-mail": { impl: "imap", sequence: 30 },
    });
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.imap.newMail.button"]).toBe(
      "Set new-mail starting point",
    );
  });

  test("keeps its record verbs in the channel form's overflow menu", () => {
    expect(messagingIntegrateImap.containers?.["messaging.Channel#actions"]).toBeUndefined();
    expect(Object.keys(channelMenu() ?? {})).toEqual([
      "messaging-integrate-imap.credential",
      "messaging-integrate-imap.new-mail",
      "messaging-integrate-imap.sample",
    ]);
  });

  test("contributes IMAP-specific connect copy", () => {
    expect(messagingIntegrateImap.i18n?.messaging?.["channel.imap.button"]).toBe("Connect IMAP");
    expect(messagingIntegrateImap.menus?.[0]?.description).toBe(
      "Connect IMAP mailbox channels",
    );
  });
});
