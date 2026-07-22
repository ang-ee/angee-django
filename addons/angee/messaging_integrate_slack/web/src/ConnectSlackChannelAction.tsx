import { CHANNEL_MODEL } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import { Button, Glyph, MutationDialog, type MutationDialogField } from "@angee/ui";
import * as React from "react";

import { ConnectSlackChannel } from "./documents";
import { useMessagingSlackT } from "./i18n";

/** User-token dialog for one internal Slack app workspace install. */
export function ConnectSlackChannelAction(): React.ReactElement {
  const t = useMessagingSlackT();
  const [open, setOpen] = React.useState(false);
  const [connect] = useAuthoredMutation(ConnectSlackChannel, {
    invalidateModels: [CHANNEL_MODEL],
  });
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.slack.name"),
        placeholder: t("channel.slack.namePlaceholder"),
        required: true,
      },
      {
        name: "token",
        label: t("channel.slack.token"),
        widget: "password",
        placeholder: t("channel.slack.tokenPlaceholder"),
        required: true,
        description: (
          <>
            <span>{t("channel.slack.tokenHelp")}</span>{" "}
            <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">
              {t("channel.slack.appsLink")}
            </a>
          </>
        ),
      },
    ],
    [t],
  );

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("channel.slack.button")}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t("channel.slack.title")}
        description={t("channel.slack.description")}
        fields={fields}
        submitLabel={t("channel.slack.submit")}
        submittingLabel={t("channel.slack.submitting")}
        cancelLabel={t("channel.slack.cancel")}
        errorFallback={t("channel.slack.error")}
        onSubmit={(values) =>
          connect({
            name: stringValue(values.name).trim(),
            token: stringValue(values.token).trim(),
          })
        }
      />
    </>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
