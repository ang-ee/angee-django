import { createNamespaceT } from "@angee/ui";

export const enMessagingSlackMessages: Record<string, string> = {
  "channel.slack.menu.label": "Slack",
  "channel.slack.menu.description": "Sync Slack workspace conversations",
  "channel.slack.button": "Connect Slack",
  "channel.slack.title": "Connect Slack",
  "channel.slack.description":
    "Create an internal Slack app from the manifest shipped with this addon, choose your workspace, install it, then paste its User OAuth token.",
  "channel.slack.name": "Connection name",
  "channel.slack.namePlaceholder": "Acme workspace",
  "channel.slack.token": "User OAuth token",
  "channel.slack.tokenPlaceholder": "xoxp-…",
  "channel.slack.tokenHelp":
    "At Slack Apps, choose Create New App → From an app manifest, paste slack-app-manifest.yaml, install the app to your workspace, and copy the User OAuth token.",
  "channel.slack.appsLink": "Open Slack Apps",
  "channel.slack.submit": "Connect",
  "channel.slack.submitting": "Verifying Slack",
  "channel.slack.cancel": "Cancel",
  "channel.slack.error": "Could not connect Slack.",
};

export const useMessagingSlackT = createNamespaceT(
  "messaging",
  enMessagingSlackMessages,
);
export type MessagingSlackT = ReturnType<typeof useMessagingSlackT>;
