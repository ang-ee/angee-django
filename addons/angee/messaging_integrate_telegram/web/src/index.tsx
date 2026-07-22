import { defineChannelBridgeAddon } from "@angee/messaging";

import { ConnectTelegramChannelAction } from "./ConnectTelegramChannelAction";
import { enMessagingTelegramMessages } from "./i18n";

export const TELEGRAM_CONNECT_ACTION_ID = "messaging-integrate-telegram.connect";
export const TELEGRAM_PAIRING_ACTION_ID = "messaging-integrate-telegram.pairing";
export const TELEGRAM_BACKEND = "telegram";

const messagingIntegrateTelegram = defineChannelBridgeAddon({
  id: "messaging-integrate-telegram",
  key: TELEGRAM_BACKEND,
  sequence: 21,
  connectAction: <ConnectTelegramChannelAction />,
  i18n: enMessagingTelegramMessages,
  instructionKey: "channel.telegram.scan",
});

export default messagingIntegrateTelegram;
