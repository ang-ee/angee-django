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

import { ConnectTelegramChannelAction } from "./ConnectTelegramChannelAction";
import { TelegramDisconnectAction } from "./TelegramDisconnectAction";
import { enMessagingTelegramMessages } from "./i18n";

export const TELEGRAM_CONNECT_ACTION_ID = "messaging-integrate-telegram.connect";
export const TELEGRAM_PAIRING_ACTION_ID = "messaging-integrate-telegram.pairing";
export const TELEGRAM_BACKEND = "telegram";

// The record-verb slot key for a channel this addon's backend owns. `messaging`
// owns `messaging.Channel`, so contributing there would displace integrate's
// verbs for *every* channel — WhatsApp's and IMAP's included — and cap the model
// at one vendor. The impl key scopes each entry to a Telegram-backed row instead.
const telegramChannelActions = formViewRecordActionsSlot(
  CHANNEL_MODEL,
  TELEGRAM_BACKEND,
);
const telegramPairingAction = (
  lifecycle: IntegrationLifecycleToken,
  labelKey: string,
  resumeOnOpen?: boolean,
) => (
  <ChannelPairingAction
    labelKey={labelKey}
    instructionKey="channel.telegram.scan"
    {...(resumeOnOpen ? { resumeOnOpen: true } : {})}
    when={integrationLifecycleIs(lifecycle)}
  />
);

const messagingIntegrateTelegram = defineBaseAddon({
  id: "messaging-integrate-telegram",
  i18n: { messaging: enMessagingTelegramMessages },
  menus: [
    {
      id: "messaging.telegram",
      label: "Telegram",
      to: "/messaging/channels",
      parentId: "messaging",
      icon: "channel",
      description: "Link Telegram accounts by QR code",
    },
  ],
  slots: [
    {
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: TELEGRAM_CONNECT_ACTION_ID,
      sequence: 21,
      content: <ConnectTelegramChannelAction />,
    },
    {
      slot: telegramChannelActions,
      id: TELEGRAM_CONNECT_ACTION_ID,
      sequence: 10,
      content: telegramPairingAction(
        "disconnected",
        "channel.pairing.connect",
        true,
      ),
    },
    {
      slot: telegramChannelActions,
      id: TELEGRAM_PAIRING_ACTION_ID,
      sequence: 10,
      content: telegramPairingAction("connected", "channel.pairing.status"),
    },
    {
      slot: telegramChannelActions,
      id: INTEGRATION_RESUME_ACTION_ID,
      sequence: 12,
      content: telegramPairingAction("paused", "channel.pairing.resume", true),
    },
    {
      slot: telegramChannelActions,
      id: INTEGRATION_DISCONNECT_ACTION_ID,
      sequence: 13,
      content: <TelegramDisconnectAction />,
    },
  ],
});

export default messagingIntegrateTelegram;
