import { createNamespaceT } from "@angee/ui";

export const enMessagingImapMessages: Record<string, string> = {
  "channel.imap.menu.label": "IMAP",
  "channel.imap.menu.description": "Connect IMAP mailbox channels",
  "channel.connect.button": "Connect IMAP",
  "channel.connect.title": "Connect IMAP",
  "channel.connect.description": "Create a message channel from an IMAP account.",
  "channel.connect.name": "Name",
  "channel.connect.namePlaceholder": "Support inbox",
  "channel.connect.host": "Host",
  "channel.connect.hostPlaceholder": "imap.example.com",
  "channel.connect.security": "Security",
  "channel.connect.securitySsl": "SSL",
  "channel.connect.securityStarttls": "STARTTLS",
  "channel.connect.securityPlain": "Plain",
  "channel.connect.port": "Port",
  "channel.connect.portPlaceholder": "Default",
  "channel.connect.username": "Username",
  "channel.connect.password": "Password",
  "channel.connect.mailboxes": "Mailboxes",
  "channel.connect.mailboxesPlaceholder": "INBOX",
  "channel.connect.mailboxesDescription": "One mailbox per line. Leave blank to auto-discover.",
  "channel.connect.ownAddresses": "Own addresses",
  "channel.connect.ownAddressesPlaceholder": "support@example.com",
  "channel.connect.ownAddressesDescription": "One address per line for inbound/outbound detection.",
  "channel.connect.submit": "Connect",
  "channel.connect.submitting": "Connecting",
  "channel.connect.cancel": "Cancel",
  "channel.connect.error": "Could not connect IMAP.",
};

export const useMessagingImapT = createNamespaceT("messaging", enMessagingImapMessages);
export type MessagingImapT = ReturnType<typeof useMessagingImapT>;
