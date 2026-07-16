import { defineBaseAddon } from "@angee/app";
import {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
  integrationLifecycleIs,
  type IntegrationLifecycleToken,
} from "@angee/integrate";
import {
  CHANNEL_MODEL,
  ChannelPairingAction,
  MESSAGING_CHANNEL_TOOLBAR_SLOT,
} from "@angee/messaging";
import { formViewRecordActionsSlot } from "@angee/ui";

import { ConnectWhatsappChannelAction } from "./ConnectWhatsappChannelAction";
import { WhatsappDisconnectAction } from "./WhatsappDisconnectAction";
import { enMessagingWhatsappMessages } from "./i18n";

export const WHATSAPP_CONNECT_ACTION_ID = "messaging-integrate-whatsapp.connect";
export const WHATSAPP_PAIRING_ACTION_ID = "messaging-integrate-whatsapp.pairing";
export const WHATSAPP_BACKEND = "whatsapp";

// The record-verb slot key for a channel this addon's backend owns. `messaging`
// owns `messaging.Channel`, so contributing there would displace integrate's
// verbs for *every* channel — IMAP's included — and cap the model at one vendor.
// The impl key scopes each entry to a WhatsApp-backed row instead.
const whatsappChannelActions = formViewRecordActionsSlot(
  CHANNEL_MODEL,
  WHATSAPP_BACKEND,
);
const whatsappPairingAction = (
  lifecycle: IntegrationLifecycleToken,
  labelKey: string,
  resumeOnOpen?: boolean,
) => (
  <ChannelPairingAction
    labelKey={labelKey}
    instructionKey="channel.whatsapp.scan"
    {...(resumeOnOpen ? { resumeOnOpen: true } : {})}
    when={integrationLifecycleIs(lifecycle)}
  />
);

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
      id: WHATSAPP_CONNECT_ACTION_ID,
      sequence: 20,
      content: <ConnectWhatsappChannelAction />,
    },
    {
      slot: whatsappChannelActions,
      id: WHATSAPP_CONNECT_ACTION_ID,
      sequence: 10,
      content: whatsappPairingAction(
        "disconnected",
        "channel.pairing.connect",
        true,
      ),
    },
    {
      slot: whatsappChannelActions,
      id: WHATSAPP_PAIRING_ACTION_ID,
      sequence: 10,
      content: whatsappPairingAction("connected", "channel.pairing.status"),
    },
    {
      slot: whatsappChannelActions,
      id: INTEGRATION_RESUME_ACTION_ID,
      sequence: 12,
      content: whatsappPairingAction("paused", "channel.pairing.resume", true),
    },
    {
      slot: whatsappChannelActions,
      id: INTEGRATION_DISCONNECT_ACTION_ID,
      sequence: 13,
      content: <WhatsappDisconnectAction />,
    },
  ],
});

export default messagingIntegrateWhatsapp;
