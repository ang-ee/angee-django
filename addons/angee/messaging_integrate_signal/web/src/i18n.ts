import { createNamespaceT } from "@angee/ui";

export const enMessagingSignalMessages: Record<string, string> = {
  "channel.signal.menu.label": "Signal",
  "channel.signal.menu.description": "Link Signal accounts by QR code",
  "channel.signal.button": "Connect Signal",
  "channel.signal.connecting": "Connecting Signal",
  "channel.signal.error": "Could not connect Signal.",
  "channel.signal.scan":
    "Open Signal on your phone → Settings → Linked Devices → Link New Device, then scan this code.",
};

export const useMessagingSignalT = createNamespaceT(
  "messaging",
  enMessagingSignalMessages,
);
export type MessagingSignalT = ReturnType<typeof useMessagingSignalT>;
