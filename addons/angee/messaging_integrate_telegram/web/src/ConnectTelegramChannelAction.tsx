import { CHANNEL_MODEL, PairingDialog } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  type MutationDialogField,
} from "@angee/ui";
import * as React from "react";

import { ConnectTelegramChannel, CreateTelegramAppKeys } from "./documents";
import { useMessagingTelegramT } from "./i18n";

/** Only application-key credentials can parameterize a Telegram client. */
const APP_KEYS_ONLY = [
  { field: "kind", operator: "eq", value: "app_keys" },
] as const;

/** Telegram application-key dialog followed by the shared channel pairing dialog. */
export function ConnectTelegramChannelAction(): React.ReactElement {
  const t = useMessagingTelegramT();
  const [open, setOpen] = React.useState(false);
  const [pairingChannelId, setPairingChannelId] = React.useState<string | null>(null);
  const [connect] = useAuthoredMutation(ConnectTelegramChannel, {
    invalidateModels: [CHANNEL_MODEL],
  });
  const [createAppKeys] = useAuthoredMutation(CreateTelegramAppKeys);
  const createFields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.telegram.keysName"),
        placeholder: t("channel.telegram.keysNamePlaceholder"),
      },
      {
        name: "app_id",
        label: t("channel.telegram.apiId"),
        kind: "integer",
        placeholder: t("channel.telegram.apiIdPlaceholder"),
      },
      {
        name: "app_secret",
        label: t("channel.telegram.apiHash"),
        widget: "password",
        placeholder: t("channel.telegram.apiHashPlaceholder"),
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
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.telegram.name"),
        placeholder: t("channel.telegram.namePlaceholder"),
        required: true,
      },
      {
        name: "credential",
        label: t("channel.telegram.credential"),
        placeholder: t("channel.telegram.credentialPlaceholder"),
        required: true,
        description: t("channel.telegram.credentialHelp"),
        relation: {
          resource: "Credential",
          filters: APP_KEYS_ONLY,
          create: {
            resource: "Credential",
            fields: createFields,
            title: t("channel.telegram.keysTitle"),
            submit: async (data) => {
              const created = await createAppKeys({
                name: stringValue(data.name).trim(),
                appId: String(data.app_id ?? "").trim(),
                appSecret: stringValue(data.app_secret).trim(),
              });
              return created?.create_credential ?? null;
            },
          },
        },
      },
    ],
    [createAppKeys, createFields, t],
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
            credentialId: stringValue(values.credential),
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
