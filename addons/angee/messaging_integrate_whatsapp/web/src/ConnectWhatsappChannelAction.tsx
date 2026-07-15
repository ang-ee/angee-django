import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import {
  Button,
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  Glyph,
  MutationDialog,
  type MutationDialogField,
} from "@angee/ui";

import {
  ConnectWhatsappChannel,
  DisconnectWhatsappChannel,
  ResetWhatsappPairing,
  WhatsappChannelPairing,
  pairingFromSyncProgress,
} from "./documents";
import { useMessagingWhatsappT } from "./i18n";

const MODEL = "messaging.Channel";

/** Button + two-step dialog contributed into the messaging channel toolbar slot. */
export function ConnectWhatsappChannelAction(): React.ReactElement {
  const t = useMessagingWhatsappT();
  const [open, setOpen] = React.useState(false);
  const [pairingChannelId, setPairingChannelId] = React.useState<string | null>(null);
  const [connect] = useAuthoredMutation(ConnectWhatsappChannel, {
    invalidateModels: [MODEL],
  });
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
          const data = await connect({ name });
          const id = data?.connect_whatsapp_channel?.id;
          if (id) setPairingChannelId(String(id));
        }}
      />
      <PairingDialog
        channelId={pairingChannelId}
        onClose={() => setPairingChannelId(null)}
      />
    </>
  );
}

/**
 * The QR pane: an authored read over the channel row, registered on the
 * messaging.Channel live bridge — every session report (QR rotation, paired,
 * logged out) lands over channelChanged and refetches this read. No polling.
 */
function PairingDialog({
  channelId,
  onClose,
}: {
  channelId: string | null;
  onClose: () => void;
}): React.ReactElement | null {
  const t = useMessagingWhatsappT();
  const { data } = useAuthoredQuery(
    WhatsappChannelPairing,
    { id: channelId ?? "" },
    { enabled: channelId !== null, models: [MODEL] },
  );
  const [resetPairing] = useAuthoredMutation(ResetWhatsappPairing, {
    invalidateModels: [MODEL],
  });
  const [disconnect] = useAuthoredMutation(DisconnectWhatsappChannel, {
    invalidateModels: [MODEL],
  });
  if (channelId === null) return null;
  const pairing = pairingFromSyncProgress(data?.channels_by_pk?.sync_progress);
  const needsRepair = pairing.state === "logged_out" || pairing.state === "stopped";
  return (
    <DialogRoot open onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent>
          <DialogTitle>{t("channel.whatsapp.pairingTitle")}</DialogTitle>
          {/* The body swaps live as channelChanged refetches; announce transitions. */}
          <DialogBody aria-live="polite">
            {pairing.state === "awaiting_scan" && pairing.qr ? (
              <>
                <p>{t("channel.whatsapp.scan")}</p>
                <img src={pairing.qr} alt={t("channel.whatsapp.qrAlt")} width={264} height={264} />
              </>
            ) : pairing.state === "paired" ? (
              <p>
                {t("channel.whatsapp.paired")}
                {pairing.phone ? ` (${pairing.phone})` : null}
              </p>
            ) : pairing.state === "logged_out" ? (
              <p>{t("channel.whatsapp.loggedOut")}</p>
            ) : pairing.state === "stopped" ? (
              <p>{t("channel.whatsapp.stopped")}</p>
            ) : (
              <p>{t("channel.whatsapp.starting")}</p>
            )}
          </DialogBody>
          <DialogFooter>
            {needsRepair ? (
              <Button variant="primary" size="sm" onClick={() => void resetPairing({ id: channelId })}>
                {t("channel.whatsapp.repair")}
              </Button>
            ) : null}
            {pairing.state === "paired" ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  void disconnect({ id: channelId });
                  onClose();
                }}
              >
                {t("channel.whatsapp.disconnect")}
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("channel.whatsapp.done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}
