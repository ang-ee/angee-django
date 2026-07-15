import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button,
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  useActionResultMutation,
} from "@angee/ui";
import * as React from "react";

import { CHANNEL_MODEL } from "./channel";
import { WhatsappChannelPairing } from "./documents";
import { useMessagingWhatsappT, type MessagingWhatsappT } from "./i18n";

/** The pairing projection the authored read returns, and its state vocabulary. */
type PairingSnapshot = DocumentType<
  typeof WhatsappChannelPairing
>["whatsapp_pairing"];
type PairingState = PairingSnapshot["state"];

/**
 * What the dialog body reports for each pairing state.
 *
 * A total `Record` rather than a ternary chain: codegen emits `PairingState` as a
 * string union (`enumsAsTypes`), so a member added to the Python `PairingState`
 * fails this declaration instead of silently falling through to "Starting…" —
 * which is the whole point of reading the generated union rather than mirroring
 * it by hand.
 */
const PAIRING_BODY: Record<
  PairingState,
  (pairing: PairingSnapshot, t: MessagingWhatsappT) => React.ReactNode
> = {
  STARTING: (_pairing, t) => <p>{t("channel.whatsapp.starting")}</p>,
  // The QR arrives a beat after the state does; until it lands this still reads
  // as starting up rather than as an empty pane.
  AWAITING_SCAN: (pairing, t) =>
    pairing.qr ? (
      <>
        <p>{t("channel.whatsapp.scan")}</p>
        <img
          src={pairing.qr}
          alt={t("channel.whatsapp.qrAlt")}
          width={264}
          height={264}
        />
      </>
    ) : (
      <p>{t("channel.whatsapp.starting")}</p>
    ),
  PAIRED: (pairing, t) => (
    <p>
      {t("channel.whatsapp.paired")}
      {pairing.phone ? ` (${pairing.phone})` : null}
    </p>
  ),
  PAUSED: (pairing, t) => (
    <p>
      {t("channel.whatsapp.paused")}
      {pairing.phone ? ` (${pairing.phone})` : null}
    </p>
  ),
  LOGGED_OUT: (_pairing, t) => <p>{t("channel.whatsapp.loggedOut")}</p>,
  STOPPED: (_pairing, t) => <p>{t("channel.whatsapp.stopped")}</p>,
  DUPLICATE_ACCOUNT: (pairing, t) => (
    <p>
      {t("channel.whatsapp.duplicate")}
      {pairing.duplicate_channel_name
        ? ` (${pairing.duplicate_channel_name})`
        : null}
    </p>
  ),
};

/** States a re-pair (wipe the device store, re-QR) is the way out of. */
const NEEDS_REPAIR: readonly PairingState[] = [
  "LOGGED_OUT",
  // Re-pair does not reconcile the *same* phone — rescanning it hits the same
  // conflict — but it is the way to link a different account on this channel,
  // which is the only move left once the scanned one is taken.
  "DUPLICATE_ACCOUNT",
];

/** States a resume (restart the session from the retained store) is the way out of. */
const CAN_RESUME: readonly PairingState[] = ["PAUSED", "STOPPED"];

/**
 * The QR pane: an authored read over the channel row, registered on the
 * messaging.Channel live bridge — every session report (QR rotation, paired,
 * logged out) lands over channelChanged and refetches this read. No polling.
 *
 * Rendered from two places — a WhatsApp channel's record-verb slot, and the
 * channel list's toolbar after Connect authors a new channel — so it binds to no
 * record context and takes the channel it pairs as a prop.
 *
 * The repair/resume verbs settle through `useActionResultMutation`, which owns the
 * failure surface: it toasts the server's own refusal message, including the
 * in-band reasons an `ok=false` outcome carries. The dialog renders no failure
 * state of its own — a local banner would replace the real reason with a generic
 * string and outlive the pairing state that produced it.
 */
export function PairingDialog({
  channelId,
  onClose,
}: {
  channelId: string | null;
  onClose: () => void;
}): React.ReactElement | null {
  const t = useMessagingWhatsappT();
  // No schema is named here: this dialog opens from a channel record's verb slot
  // *and* from the channel list's toolbar, and both hooks resolve the active data
  // provider from ambient context. Hardcoding one was wrong in both places.
  const { data } = useAuthoredQuery(
    WhatsappChannelPairing,
    { id: channelId ?? "" },
    { enabled: channelId !== null, models: [CHANNEL_MODEL] },
  );
  // Both verbs move the channel's own lifecycle, so they invalidate the same
  // targets the authored read above is registered against.
  const [resetPairing] = useActionResultMutation("reset_whatsapp_pairing", {
    invalidateModels: [CHANNEL_MODEL],
  });
  const [resume] = useActionResultMutation("resume_whatsapp_pairing", {
    invalidateModels: [CHANNEL_MODEL],
  });
  if (channelId === null) return null;
  // `PairingState` is a StrEnum: the read wire value is the upper-case member
  // name, not the lower-case token the session serializes into its report.
  const pairing: PairingSnapshot = data?.whatsapp_pairing ?? {
    state: "STARTING",
    qr: "",
    phone: "",
    duplicate_channel_name: "",
  };
  return (
    <DialogRoot open onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent>
          <DialogTitle>{t("channel.whatsapp.pairingTitle")}</DialogTitle>
          {/* The body swaps live as channelChanged refetches; announce transitions. */}
          <DialogBody aria-live="polite">
            {PAIRING_BODY[pairing.state](pairing, t)}
          </DialogBody>
          <DialogFooter>
            {NEEDS_REPAIR.includes(pairing.state) ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  void resetPairing(channelId);
                }}
              >
                {t("channel.whatsapp.repair")}
              </Button>
            ) : null}
            {CAN_RESUME.includes(pairing.state) ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  void resume(channelId);
                }}
              >
                {t("channel.whatsapp.resume")}
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
