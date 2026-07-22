import { defineChannelBridgeAddon } from "@angee/messaging";

import { ConnectWhatsappChannelAction } from "./ConnectWhatsappChannelAction";
import { enMessagingWhatsappMessages } from "./i18n";

export const WHATSAPP_CONNECT_ACTION_ID = "messaging-integrate-whatsapp.connect";
export const WHATSAPP_PAIRING_ACTION_ID = "messaging-integrate-whatsapp.pairing";
export const WHATSAPP_BACKEND = "whatsapp";

const messagingIntegrateWhatsapp = defineChannelBridgeAddon({
  id: "messaging-integrate-whatsapp",
  key: WHATSAPP_BACKEND,
  sequence: 20,
  connectAction: <ConnectWhatsappChannelAction />,
  i18n: enMessagingWhatsappMessages,
  instructionKey: "channel.whatsapp.scan",
});

export default messagingIntegrateWhatsapp;
