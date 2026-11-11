import { expectChannelVerbsScoped, expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateImap from "./index";

describe("messaging_integrate_imap addon manifest", () => {
  test("satisfies the channel-bridge invariants and scopes its verbs to IMAP rows", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateImap)).not.toThrow();
    expect(() => expectChannelVerbsScoped(messagingIntegrateImap, "imap")).not.toThrow();
  });
});
