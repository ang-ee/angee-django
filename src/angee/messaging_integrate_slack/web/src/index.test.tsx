import { expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateSlack from "./index";

describe("messaging_integrate_slack addon manifest", () => {
  test("satisfies the channel-bridge invariants", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateSlack)).not.toThrow();
  });
});
