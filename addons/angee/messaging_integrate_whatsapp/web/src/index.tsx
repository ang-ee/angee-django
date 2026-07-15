import { defineBaseAddon } from "@angee/app";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";

import { ConnectWhatsappChannelAction } from "./ConnectWhatsappChannelAction";
import { enMessagingWhatsappMessages } from "./i18n";

const messagingIntegrateWhatsapp = defineBaseAddon({
  id: "messaging-integrate-whatsapp",
  i18n: { messaging: enMessagingWhatsappMessages },
  menus: [
    {
      id: "messaging.whatsapp",
      label: "WhatsApp",
      to: "/messaging/channels",
      parentId: "messaging",
      icon: "channel",
      description: "Link WhatsApp accounts by QR code",
    },
  ],
  slots: [
    {
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-whatsapp.connect",
      sequence: 20,
      content: <ConnectWhatsappChannelAction />,
    },
  ],
});

export default messagingIntegrateWhatsapp;
