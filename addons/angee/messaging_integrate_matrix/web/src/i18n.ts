import { createNamespaceT } from "@angee/ui";

export const enMessagingMatrixMessages: Record<string, string> = {
  "channel.matrix.menu.label": "Matrix",
  "channel.matrix.menu.description": "Connect your Matrix account",
  "channel.matrix.button": "Connect Matrix",
  "channel.matrix.title": "Connect Matrix",
  "channel.matrix.description":
    "Sign in to your Matrix homeserver. Your password remains the durable login credential; the optional recovery key is consumed after device verification.",
  "channel.matrix.homeserver": "Homeserver URL",
  "channel.matrix.homeserverPlaceholder": "https://matrix.example.com",
  "channel.matrix.username": "Matrix user ID or username",
  "channel.matrix.usernamePlaceholder": "@ada:example.com",
  "channel.matrix.password": "Password",
  "channel.matrix.submit": "Connect",
  "channel.matrix.submitting": "Connecting Matrix",
  "channel.matrix.cancel": "Cancel",
  "channel.matrix.error": "Could not connect Matrix.",
  "channel.matrix.recovery":
    "After sign-in, enter your Matrix recovery key to verify this device and unlock encrypted history. You may skip and continue with forward-only decryption.",
};

export const useMessagingMatrixT = createNamespaceT(
  "messaging",
  enMessagingMatrixMessages,
);
export type MessagingMatrixT = ReturnType<typeof useMessagingMatrixT>;
