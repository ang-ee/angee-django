import { usePairingConnect } from "@angee/messaging";
import { Button, Glyph, MutationDialog, type MutationDialogField } from "@angee/ui";
import * as React from "react";

import { ConnectDiscordChannel } from "./documents";
import { useMessagingDiscordT } from "./i18n";

const INVITE_PERMISSIONS = "66560";
const APPLICATION_ID_PATTERN = /^\d{17,20}$/;

/** Bot-token dialog followed by shared STARTING → PAIRED status. */
export function ConnectDiscordChannelAction(): React.ReactElement {
  const t = useMessagingDiscordT();
  const [open, setOpen] = React.useState(false);
  const { connect, pairingDialog } = usePairingConnect(
    ConnectDiscordChannel,
    "connect_discord_channel",
  );
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.discord.name"),
        placeholder: t("channel.discord.namePlaceholder"),
        required: true,
      },
      {
        name: "application_id",
        label: t("channel.discord.applicationId"),
        placeholder: t("channel.discord.applicationIdPlaceholder"),
        required: true,
        description: t("channel.discord.applicationIdHelp"),
      },
      {
        name: "token",
        label: t("channel.discord.token"),
        widget: "password",
        required: true,
        description: t("channel.discord.tokenHelp"),
      },
    ],
    [t],
  );

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("channel.discord.button")}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t("channel.discord.title")}
        description={t("channel.discord.description")}
        fields={fields}
        submitLabel={t("channel.discord.submit")}
        submittingLabel={t("channel.discord.submitting")}
        cancelLabel={t("channel.discord.cancel")}
        errorFallback={t("channel.discord.error")}
        onSubmit={async (values) => {
          const applicationId = stringValue(values.application_id).trim();
          if (!APPLICATION_ID_PATTERN.test(applicationId)) {
            throw new Error(t("channel.discord.applicationIdInvalid"));
          }
          await connect(
            {
              name: stringValue(values.name).trim(),
              token: stringValue(values.token).trim(),
            },
            () => (
              <p>
                <a
                  href={discordBotInviteUrl(applicationId)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("channel.discord.invite")}
                </a>
              </p>
            ),
          );
        }}
      />
      {pairingDialog}
    </>
  );
}

export function discordBotInviteUrl(applicationId: string): string {
  if (!APPLICATION_ID_PATTERN.test(applicationId)) {
    throw new TypeError("A Discord application ID must be a 17–20 digit snowflake.");
  }
  return `https://discord.com/oauth2/authorize?client_id=${encodeURIComponent(applicationId)}&scope=bot&permissions=${INVITE_PERMISSIONS}`;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
