import { defineBaseAddon } from "@angee/app";
import {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
} from "@angee/integrate";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { formViewRecordActionsSlot } from "@angee/ui";

import { CHANNEL_MODEL, WHATSAPP_BACKEND } from "./channel";
import { ConnectWhatsappChannelAction } from "./ConnectWhatsappChannelAction";
import {
  WHATSAPP_CONNECT_ACTION_ID,
  WHATSAPP_PAIRING_ACTION_ID,
  WhatsappConnectionAction,
} from "./WhatsappConnectionAction";
import { WhatsappDisconnectAction } from "./WhatsappDisconnectAction";
import { enMessagingWhatsappMessages } from "./i18n";

// The record-verb slot key for a channel this addon's backend owns. `messaging`
// owns `messaging.Channel`, so contributing there would displace integrate's
// verbs for *every* channel — IMAP's included — and cap the model at one vendor.
// The impl key scopes each entry to a WhatsApp-backed row instead.
const whatsappChannelActions = formViewRecordActionsSlot(
  CHANNEL_MODEL,
  WHATSAPP_BACKEND,
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
    // Connecting a channel is a QR pairing, not a flag flip, so it has no shared
    // default to specialize and carries this addon's own id.
    {
      slot: whatsappChannelActions,
      id: WHATSAPP_CONNECT_ACTION_ID,
      sequence: 10,
      content: <WhatsappConnectionAction lifecycle="disconnected" />,
    },
    // A logged-out or duplicate-rejected channel keeps its CONNECTED lifecycle, so
    // the disconnected and paused entries never reach it — this is the console's
    // only way into the dialog that offers the repair for exactly those states.
    {
      slot: whatsappChannelActions,
      id: WHATSAPP_PAIRING_ACTION_ID,
      // Shares Connect's sequence: the two gate on opposite lifecycles, so they
      // never render together, and each leads the cluster on the row it serves.
      sequence: 10,
      content: <WhatsappConnectionAction lifecycle="connected" />,
    },
    // These two carry integrate's ids, so each replaces the inherited verb for a
    // WhatsApp channel only — the vendor owns those two transitions.
    {
      slot: whatsappChannelActions,
      id: INTEGRATION_RESUME_ACTION_ID,
      sequence: 12,
      content: <WhatsappConnectionAction lifecycle="paused" />,
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
