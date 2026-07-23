import { createNamespaceT } from "@angee/ui";

export const enMessagingDiscordMessages: Record<string, string> = {
  "channel.discord.menu.label": "Discord",
  "channel.discord.menu.description": "Connect an invited Discord bot",
  "channel.discord.button": "Connect Discord",
  "channel.discord.title": "Connect Discord",
  "channel.discord.description":
    "Discord ingests the servers you invite the bot to; it cannot read your private DMs.",
  "channel.discord.name": "Connection name",
  "channel.discord.namePlaceholder": "Community bot",
  "channel.discord.applicationId": "Bot application ID",
  "channel.discord.applicationIdPlaceholder": "123456789012345678",
  "channel.discord.applicationIdHelp":
    "Find the application ID in the Discord Developer Portal. Angee uses it only to build the bot invite link.",
  "channel.discord.applicationIdInvalid":
    "Bot application ID must be a 17–20 digit Discord snowflake.",
  "channel.discord.token": "Bot token",
  "channel.discord.tokenHelp":
    "Enable the Message Content intent in the Discord Developer Portal, reset or copy the bot token, and paste it here.",
  "channel.discord.invite": "Invite this bot to Discord servers",
  "channel.discord.submit": "Connect",
  "channel.discord.submitting": "Connecting Discord",
  "channel.discord.cancel": "Cancel",
  "channel.discord.error": "Could not connect Discord.",
};

export const useMessagingDiscordT = createNamespaceT(
  "messaging",
  enMessagingDiscordMessages,
);
export type MessagingDiscordT = ReturnType<typeof useMessagingDiscordT>;
