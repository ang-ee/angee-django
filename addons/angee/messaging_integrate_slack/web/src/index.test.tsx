import { expectValidChannelBridgeAddon } from "@angee/messaging/testing";
import { describe, expect, test } from "vitest";

import messagingIntegrateSlack from "./index";

describe("messaging_integrate_slack addon manifest", () => {
  test("declares one poll connect contribution with no pairing verbs", () => {
    expect(() => expectValidChannelBridgeAddon(messagingIntegrateSlack)).not.toThrow();
    expect(messagingIntegrateSlack.containers?.["messaging.channels#toolbar"])
      .toMatchObject({ "messaging-integrate-slack.connect": { sequence: 24 } });
    // A poll bridge adds no pairing verbs to the channel form's toolbar.
    expect(messagingIntegrateSlack.containers?.["messaging.Channel#actions"]).toBeUndefined();
  });

  test("contributes Slack-specific manifest instructions", () => {
    expect(messagingIntegrateSlack.i18n?.messaging?.["channel.slack.tokenHelp"]).toContain(
      "slack-app-manifest.yaml",
    );
  });
});
