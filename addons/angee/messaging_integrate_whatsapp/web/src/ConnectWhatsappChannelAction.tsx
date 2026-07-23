import { usePairingConnect } from "@angee/messaging";
import * as React from "react";
import {
  Button,
  Glyph,
  MutationDialog,
  type MutationDialogField,
} from "@angee/ui";

import { ConnectWhatsappChannel } from "./documents";
import { useMessagingWhatsappT } from "./i18n";

/** Button + two-step dialog contributed into the messaging channel toolbar slot. */
export function ConnectWhatsappChannelAction(): React.ReactElement {
  const t = useMessagingWhatsappT();
  const [open, setOpen] = React.useState(false);
  const { connect, pairingDialog } = usePairingConnect(
    ConnectWhatsappChannel,
    "connect_whatsapp_channel",
    t("channel.whatsapp.scan"),
  );
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.whatsapp.name"),
        placeholder: t("channel.whatsapp.namePlaceholder"),
        required: true,
      },
    ],
    [t],
  );
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("channel.whatsapp.button")}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t("channel.whatsapp.title")}
        description={t("channel.whatsapp.description")}
        fields={fields}
        submitLabel={t("channel.whatsapp.submit")}
        submittingLabel={t("channel.whatsapp.submitting")}
        cancelLabel={t("channel.whatsapp.cancel")}
        errorFallback={t("channel.whatsapp.error")}
        onSubmit={async (values) => {
          const name = typeof values.name === "string" ? values.name.trim() : "";
          await connect({ name });
        }}
      />
      {pairingDialog}
    </>
  );
}
