import { expectValidBaseAddon } from "@angee/app/testing";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { describe, expect, test } from "vitest";

import messagingIntegrateSlack from "./index";

describe("messaging_integrate_slack addon manifest", () => {
  test("declares one poll connect contribution with no pairing verbs", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateSlack)).not.toThrow();
    expect(messagingIntegrateSlack.slots).toHaveLength(1);
    expect(messagingIntegrateSlack.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-slack.connect",
      sequence: 24,
    });
  });

  test("contributes Slack navigation and manifest instructions", () => {
    expect(messagingIntegrateSlack.menus?.[0]).toMatchObject({
      id: "messaging.slack",
      label: "Slack",
      parentId: "messaging",
      description: "Sync Slack workspace conversations",
    });
    expect(messagingIntegrateSlack.i18n?.messaging?.["channel.slack.tokenHelp"]).toContain(
      "slack-app-manifest.yaml",
    );
  });
});
