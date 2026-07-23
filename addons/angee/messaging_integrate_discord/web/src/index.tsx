import { defineChannelBridgeAddon } from "@angee/messaging";

import { ConnectDiscordChannelAction } from "./ConnectDiscordChannelAction";
import { enMessagingDiscordMessages } from "./i18n";

export const DISCORD_BACKEND = "discord";

const messagingIntegrateDiscord = defineChannelBridgeAddon({
  id: "messaging-integrate-discord",
  key: DISCORD_BACKEND,
  sequence: 25,
  connectAction: <ConnectDiscordChannelAction />,
  i18n: enMessagingDiscordMessages,
});

export default messagingIntegrateDiscord;
