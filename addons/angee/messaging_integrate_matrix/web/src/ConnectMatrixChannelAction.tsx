import { usePairingConnect } from "@angee/messaging";
import { Button, Glyph, MutationDialog, type MutationDialogField } from "@angee/ui";
import * as React from "react";

import { ConnectMatrixChannel } from "./documents";
import { useMessagingMatrixT } from "./i18n";

/** Matrix login dialog followed by the shared optional-secret pairing dialog. */
export function ConnectMatrixChannelAction(): React.ReactElement {
  const t = useMessagingMatrixT();
  const [open, setOpen] = React.useState(false);
  const { connect, pairingDialog } = usePairingConnect(
    ConnectMatrixChannel,
    "connect_matrix_channel",
    t("channel.matrix.recovery"),
  );
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
          await connect({
            homeserver: stringValue(values.homeserver).trim(),
            username: stringValue(values.username).trim(),
            password: stringValue(values.password),
          });
        }}
      />
      {pairingDialog}
    </>
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
