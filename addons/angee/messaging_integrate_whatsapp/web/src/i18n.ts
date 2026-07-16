import { createNamespaceT } from "@angee/ui";

export const enMessagingWhatsappMessages: Record<string, string> = {
  "channel.whatsapp.button": "Connect WhatsApp",
  "channel.whatsapp.title": "Connect WhatsApp",
  "channel.whatsapp.description": "Link a WhatsApp account by scanning a QR code with your phone.",
  "channel.whatsapp.name": "Name",
  "channel.whatsapp.namePlaceholder": "Personal WhatsApp",
  "channel.whatsapp.submit": "Connect",
  "channel.whatsapp.submitting": "Connecting",
  "channel.whatsapp.cancel": "Cancel",
  "channel.whatsapp.error": "Could not connect WhatsApp.",
  "channel.whatsapp.scan": "Open WhatsApp on your phone → Linked devices → Link a device, then scan this code.",
  "channel.whatsapp.disconnect": "Disconnect",
  "channel.whatsapp.disconnectConfirm.title": "Disconnect this WhatsApp channel?",
  "channel.whatsapp.disconnectConfirm.body":
    "The live session stops and the account is released. The linked device is retained, so you can reconnect without scanning again.",
};

export const useMessagingWhatsappT = createNamespaceT("messaging", enMessagingWhatsappMessages);
export type MessagingWhatsappT = ReturnType<typeof useMessagingWhatsappT>;
