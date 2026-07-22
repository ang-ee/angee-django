import { createNamespaceT } from "@angee/ui";

export const enMessagingTelegramMessages: Record<string, string> = {
  "channel.telegram.menu.label": "Telegram",
  "channel.telegram.menu.description": "Link Telegram accounts by QR code",
  "channel.telegram.button": "Connect Telegram",
  "channel.telegram.title": "Connect Telegram",
  "channel.telegram.description":
    "Link a Telegram account with application keys and a QR code.",
  "channel.telegram.name": "Name",
  "channel.telegram.namePlaceholder": "Personal Telegram",
  "channel.telegram.credential": "Application keys",
  "channel.telegram.credentialPlaceholder": "Select or create application keys",
  "channel.telegram.credentialHelp":
    "One Telegram application registration works for any number of accounts, so keys are shared across channels.",
  "channel.telegram.keysTitle": "New Telegram application keys",
  "channel.telegram.keysName": "Name",
  "channel.telegram.keysNamePlaceholder": "My Telegram application",
  "channel.telegram.apiId": "API ID",
  "channel.telegram.apiIdPlaceholder": "123456",
  "channel.telegram.apiHash": "API hash",
  "channel.telegram.apiHashPlaceholder": "Application API hash",
  "channel.telegram.keysHelp": "Create or copy your Telegram application keys.",
  "channel.telegram.keysLink": "my.telegram.org",
  "channel.telegram.submit": "Connect",
  "channel.telegram.submitting": "Connecting",
  "channel.telegram.cancel": "Cancel",
  "channel.telegram.error": "Could not connect Telegram.",
  "channel.telegram.scan":
    "Open Telegram on your phone → Settings → Devices → Link Desktop Device, then scan this code.",
};

export const useMessagingTelegramT = createNamespaceT(
  "messaging",
  enMessagingTelegramMessages,
);
export type MessagingTelegramT = ReturnType<typeof useMessagingTelegramT>;
