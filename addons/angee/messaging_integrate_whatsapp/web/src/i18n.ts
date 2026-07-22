import { createNamespaceT } from "@angee/ui";

export const enMessagingWhatsappMessages: Record<string, string> = {
  "channel.whatsapp.menu.label": "WhatsApp",
  "channel.whatsapp.menu.description": "Link WhatsApp accounts by QR code",
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
};

export const useMessagingWhatsappT = createNamespaceT("messaging", enMessagingWhatsappMessages);
export type MessagingWhatsappT = ReturnType<typeof useMessagingWhatsappT>;
