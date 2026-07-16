import { CHANNEL_MODEL, PairingDialog } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  type MutationDialogField,
} from "@angee/ui";
import * as React from "react";

import { ConnectTelegramChannel } from "./documents";
import { useMessagingTelegramT } from "./i18n";

/** Telegram application-key dialog followed by the shared channel pairing dialog. */
export function ConnectTelegramChannelAction(): React.ReactElement {
  const t = useMessagingTelegramT();
  const [open, setOpen] = React.useState(false);
  const [pairingChannelId, setPairingChannelId] = React.useState<string | null>(null);
  const [connect] = useAuthoredMutation(ConnectTelegramChannel, {
    invalidateModels: [CHANNEL_MODEL],
  });
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.telegram.name"),
        placeholder: t("channel.telegram.namePlaceholder"),
        required: true,
      },
      {
        name: "api_id",
        label: t("channel.telegram.apiId"),
        kind: "integer",
        placeholder: t("channel.telegram.apiIdPlaceholder"),
        required: true,
      },
      {
        name: "api_hash",
        label: t("channel.telegram.apiHash"),
        widget: "password",
        placeholder: t("channel.telegram.apiHashPlaceholder"),
        required: true,
        description: (
          <>
            <span>{t("channel.telegram.keysHelp")}</span>
            <br />
            <a href="https://my.telegram.org/" target="_blank" rel="noreferrer">
              {t("channel.telegram.keysLink")}
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
        {t("channel.telegram.button")}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t("channel.telegram.title")}
        description={t("channel.telegram.description")}
        fields={fields}
        submitLabel={t("channel.telegram.submit")}
        submittingLabel={t("channel.telegram.submitting")}
        cancelLabel={t("channel.telegram.cancel")}
        errorFallback={t("channel.telegram.error")}
        onSubmit={async (values) => {
          const data = await connect({
            name: stringValue(values.name).trim(),
            apiId: String(values.api_id ?? "").trim(),
            apiHash: stringValue(values.api_hash).trim(),
          });
          const id = data?.connect_telegram_channel?.id;
          if (id) setPairingChannelId(String(id));
        }}
      />
      <PairingDialog
        channelId={pairingChannelId}
        instruction={t("channel.telegram.scan")}
        onClose={() => setPairingChannelId(null)}
      />
    </>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
