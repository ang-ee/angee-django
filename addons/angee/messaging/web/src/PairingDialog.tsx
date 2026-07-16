import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
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
  ErrorBanner,
  Glyph,
  useActionResultMutation,
  useRecordChromeActionMutation,
  useRecordChromeContext,
  type RecordChromeContext,
} from "@angee/ui";
import * as React from "react";

import { CHANNEL_MODEL, ChannelPairing } from "./documents";
import { useMessagingT, type MessagingT } from "./i18n";

/** The pairing projection the authored read returns, and its state vocabulary. */
export type PairingSnapshot = DocumentType<
  typeof ChannelPairing
>["channel_pairing"];
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
  (pairing: PairingSnapshot, t: MessagingT, instruction: string) => React.ReactNode
> = {
  STARTING: (_pairing, t) => <p>{t("channel.pairing.starting")}</p>,
  // The QR arrives a beat after the state does; until it lands this still reads
  // as starting up rather than as an empty pane.
  AWAITING_SCAN: (pairing, t, instruction) =>
    pairing.qr ? (
      <>
        <p>{instruction}</p>
        <img
          src={pairing.qr}
          alt={t("channel.pairing.qrAlt")}
          width={264}
          height={264}
        />
      </>
    ) : (
      <p>{t("channel.pairing.starting")}</p>
    ),
  PAIRED: (pairing, t) => (
    <p>
      {t("channel.pairing.paired")}
      {pairing.account_label ? ` (${pairing.account_label})` : null}
    </p>
  ),
  PAUSED: (pairing, t) => (
    <p>
      {t("channel.pairing.paused")}
      {pairing.account_label ? ` (${pairing.account_label})` : null}
    </p>
  ),
  LOGGED_OUT: (_pairing, t) => <p>{t("channel.pairing.loggedOut")}</p>,
  STOPPED: (_pairing, t) => <p>{t("channel.pairing.stopped")}</p>,
  DUPLICATE_ACCOUNT: (pairing, t) => (
    <p>
      {t("channel.pairing.duplicate")}
      {pairing.duplicate_channel_name
        ? ` (${pairing.duplicate_channel_name})`
        : null}
    </p>
  ),
};

/** States a re-pair (wipe the device store, re-QR) is the way out of. */
const NEEDS_REPAIR: readonly PairingState[] = [
  "LOGGED_OUT",
  // Re-pair does not reconcile the same account — rescanning it hits the same
  // conflict — but it is the way to link a different account on this channel,
  // which is the only move left once the scanned one is taken.
  "DUPLICATE_ACCOUNT",
];

/** States a resume (restart the session from the retained store) is the way out of. */
const CAN_RESUME: readonly PairingState[] = ["PAUSED", "STOPPED"];

type LoadedRecordChromeContext = RecordChromeContext & {
  record: NonNullable<RecordChromeContext["record"]>;
};

/** Open pairing from a channel record, resuming only when intent is absent. */
export function ChannelPairingAction({
  labelKey,
  instructionKey,
  resumeOnOpen = false,
  when,
}: {
  /** Messaging-namespace key for the action label. */
  labelKey: string;
  /** Messaging-namespace key contributed by the channel backend addon. */
  instructionKey: string;
  resumeOnOpen?: boolean;
  when: (context: LoadedRecordChromeContext) => boolean;
}): React.ReactElement | null {
  const t = useMessagingT();
  const context = useRecordChromeContext();
  const { recordId, record } = context;
  const [dialogId, setDialogId] = React.useState<string | null>(null);
  const [resume] = useRecordChromeActionMutation<ActionFieldName>(
    "resume_channel_pairing",
  );

  const openConnection = React.useCallback((): void => {
    setDialogId(recordId);
    if (resumeOnOpen) void resume(recordId);
  }, [recordId, resume, resumeOnOpen]);

  // A live push hides the lifecycle-gated button after resume. Keep the dialog
  // mounted until the operator closes it so the arriving QR remains visible.
  const showButton = record !== null && when({ ...context, record });
  if (!showButton && dialogId === null) return null;

  return (
    <>
      {showButton ? (
        <Button
          variant={resumeOnOpen ? "primary" : "secondary"}
          size="sm"
          onClick={openConnection}
        >
          <Glyph decorative name="link" />
          {t(labelKey)}
        </Button>
      ) : null}
      <PairingDialog
        channelId={dialogId}
        instruction={t(instructionKey)}
        onClose={() => setDialogId(null)}
      />
    </>
  );
}

/**
 * The QR pane: an authored read over the channel row, registered on the
 * messaging.Channel live bridge — every session report (QR rotation, paired,
 * logged out) lands over channelChanged and refetches this read. No polling.
 *
 * Rendered from two places — a live channel's record-verb slot, and the channel
 * list's toolbar after a backend authors a new channel — so it binds to no record
 * context and takes the channel it pairs as a prop.
 *
 * The repair/resume verbs settle through `useActionResultMutation`, which owns the
 * mutation failure surface: it toasts the server's own refusal message, including
 * the in-band reasons an `ok=false` outcome carries. The authored read's transport
 * failure is distinct and renders through the shared ErrorBanner in the body.
 */
export function PairingDialog({
  channelId,
  instruction,
  onClose,
}: {
  channelId: string | null;
  instruction: string;
  onClose: () => void;
}): React.ReactElement | null {
  const t = useMessagingT();
  // No schema is named here: this dialog opens from a channel record's verb slot
  // *and* from the channel list's toolbar, and both hooks resolve the active data
  // provider from ambient context. Hardcoding one was wrong in both places.
  const { data, error } = useAuthoredQuery(
    ChannelPairing,
    { id: channelId ?? "" },
    { enabled: channelId !== null, models: [CHANNEL_MODEL] },
  );
  // Both verbs move the channel model. The action owner invalidates its refine
  // resource caches and sweeps authored reads registered for the same model,
  // including ChannelPairing above.
  const [resetPairing, resetState] = useActionResultMutation<ActionFieldName>(
    "reset_channel_pairing",
    { invalidateModels: [CHANNEL_MODEL] },
  );
  const [resume, resumeState] = useActionResultMutation<ActionFieldName>(
    "resume_channel_pairing",
    { invalidateModels: [CHANNEL_MODEL] },
  );
  if (channelId === null) return null;
  // `PairingState` is a StrEnum: the read wire value is the upper-case member
  // name, not the lower-case token the session serializes into its report.
  const pairing: PairingSnapshot = data?.channel_pairing ?? {
    state: "STARTING",
    qr: "",
    account_label: "",
    duplicate_channel_name: "",
  };
  return (
    <DialogRoot open onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent>
          <DialogTitle>{t("channel.pairing.title")}</DialogTitle>
          {/* The body swaps live as channelChanged refetches; announce transitions. */}
          <DialogBody aria-live="polite">
            {error ? (
              <ErrorBanner description={error.message} />
            ) : (
              (PAIRING_BODY[pairing.state] ?? PAIRING_BODY.STARTING)(
                pairing,
                t,
                instruction,
              )
            )}
          </DialogBody>
          <DialogFooter>
            {NEEDS_REPAIR.includes(pairing.state) || resetState.fetching ? (
              <Button
                variant="primary"
                size="sm"
                disabled={resetState.fetching}
                onClick={() => {
                  void resetPairing(channelId);
                }}
              >
                {t("channel.pairing.repair")}
              </Button>
            ) : null}
            {CAN_RESUME.includes(pairing.state) || resumeState.fetching ? (
              <Button
                variant="primary"
                size="sm"
                disabled={resumeState.fetching}
                onClick={() => {
                  void resume(channelId);
                }}
              >
                {t("channel.pairing.resume")}
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("channel.pairing.done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}
