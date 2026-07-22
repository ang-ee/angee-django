import { CHANNEL_MODEL, PairingDialog } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import { Button, Glyph, MutationDialog, type MutationDialogField } from "@angee/ui";
import * as React from "react";

import { ConnectMatrixChannel } from "./documents";
import { useMessagingMatrixT } from "./i18n";

/** Matrix login dialog followed by the shared optional-secret pairing dialog. */
export function ConnectMatrixChannelAction(): React.ReactElement {
  const t = useMessagingMatrixT();
  const [open, setOpen] = React.useState(false);
  const [pairingChannelId, setPairingChannelId] = React.useState<string | null>(null);
  const [connect] = useAuthoredMutation(ConnectMatrixChannel, {
    invalidateModels: [CHANNEL_MODEL],
  });
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "homeserver",
        label: t("channel.matrix.homeserver"),
        placeholder: t("channel.matrix.homeserverPlaceholder"),
        required: true,
      },
      {
        name: "username",
        label: t("channel.matrix.username"),
        placeholder: t("channel.matrix.usernamePlaceholder"),
        required: true,
      },
      {
        name: "password",
        label: t("channel.matrix.password"),
        widget: "password",
        required: true,
      },
    ],
    [t],
  );

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("channel.matrix.button")}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t("channel.matrix.title")}
        description={t("channel.matrix.description")}
        fields={fields}
        submitLabel={t("channel.matrix.submit")}
        submittingLabel={t("channel.matrix.submitting")}
        cancelLabel={t("channel.matrix.cancel")}
        errorFallback={t("channel.matrix.error")}
        onSubmit={async (values) => {
          const data = await connect({
            homeserver: stringValue(values.homeserver).trim(),
            username: stringValue(values.username).trim(),
            password: stringValue(values.password),
          });
          const id = data?.connect_matrix_channel?.id;
          if (id) setPairingChannelId(String(id));
        }}
      />
      <PairingDialog
        channelId={pairingChannelId}
        instruction={t("channel.matrix.recovery")}
        onClose={() => setPairingChannelId(null)}
      />
    </>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
