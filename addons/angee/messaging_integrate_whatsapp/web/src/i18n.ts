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
  "channel.whatsapp.pairingTitle": "Link your WhatsApp",
  "channel.whatsapp.starting": "Starting the pairing session…",
  "channel.whatsapp.scan": "Open WhatsApp on your phone → Linked devices → Link a device, then scan this code.",
  "channel.whatsapp.qrAlt": "WhatsApp pairing QR code",
  "channel.whatsapp.paired": "Linked! Your chats are syncing.",
  "channel.whatsapp.stopped": "The pairing session stopped before completing.",
  "channel.whatsapp.loggedOut": "This device was unlinked from the phone.",
  "channel.whatsapp.repair": "Re-pair",
  "channel.whatsapp.disconnect": "Disconnect",
  "channel.whatsapp.done": "Done",
};

export const useMessagingWhatsappT = createNamespaceT("messaging", enMessagingWhatsappMessages);
export type MessagingWhatsappT = ReturnType<typeof useMessagingWhatsappT>;
