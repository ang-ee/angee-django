import { expectValidBaseAddon } from "@angee/app/testing";
import { CHANNEL_MODEL, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { formViewRecordActionsSlot } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { DISCORD_BACKEND, default as messagingIntegrateDiscord } from "./index";

describe("messaging_integrate_discord addon manifest", () => {
  test("declares a live bridge with no QR instruction", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateDiscord)).not.toThrow();
    expect(messagingIntegrateDiscord.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-discord.connect",
      sequence: 25,
    });
    const actions = (messagingIntegrateDiscord.slots ?? []).slice(1);
    expect(actions.map((entry) => entry.slot)).toEqual(
      actions.map(() => formViewRecordActionsSlot(CHANNEL_MODEL, DISCORD_BACKEND)),
    );
  });

  test("states the bot's guild-scoped visibility wall", () => {
    expect(messagingIntegrateDiscord.i18n?.messaging?.["channel.discord.description"]).toBe(
      "Discord ingests the servers you invite the bot to; it cannot read your private DMs.",
    );
  });
});
