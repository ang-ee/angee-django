import { integrationLifecycle } from "@angee/integrate";
import {
  Button,
  Glyph,
  useRecordChromeActionMutation,
  useRecordChromeContext,
} from "@angee/ui";
import * as React from "react";

import { PairingDialog } from "./PairingDialog";
import { useMessagingWhatsappT } from "./i18n";

/**
 * Connecting a channel is a QR pairing rather than a flag flip, so it has no
 * shared default to specialize — this id is this addon's own. It names the addon's
 * connect affordance in both slots it reaches: the channel-list toolbar (author a
 * new channel) and a disconnected channel's record verbs.
 */
export const WHATSAPP_CONNECT_ACTION_ID = "messaging-integrate-whatsapp.connect";

/**
 * Reading a connected channel's pairing is this addon's own affordance too: no
 * lifecycle moves, so there is no shared verb to specialize.
 */
export const WHATSAPP_PAIRING_ACTION_ID = "messaging-integrate-whatsapp.pairing";

/** The lifecycle each contributed entry gates on, and what its button says. */
const BUTTON_LABEL_KEY = {
  disconnected: "channel.whatsapp.connect",
  paused: "channel.whatsapp.resume",
  connected: "channel.whatsapp.pairing",
} as const;

type WhatsappConnectionLifecycle = keyof typeof BUTTON_LABEL_KEY;

/**
 * Open a WhatsApp channel's QR pairing dialog, declaring the connection first
 * where the lifecycle has not been declared yet.
 *
 * Contributed against the `messaging.Channel`/`whatsapp` impl key, so the slot key
 * settles both the model *and* the backend: this only ever renders for a channel
 * whose `backend_class` is WhatsApp, and a channel this addon does not own (IMAP)
 * keeps integrate's own verbs untouched. Lifecycle is the one fact left to gate on,
 * and it picks both the label and whether the click declares intent:
 *
 * - `disconnected`/`paused` — the operator has not asked for this channel, so the
 *   click fires `resume_whatsapp_pairing` (lifecycle CONNECTED + start the session)
 *   and opens the dialog on the session it just asked for.
 * - `connected` — the intent already stands, so the click only reads. A logged-out
 *   or duplicate-rejected channel *stays* CONNECTED (the worker reports runtime
 *   state; it does not overwrite the operator's declared intent), so neither entry
 *   above reaches it, and `ensure_sessions` will not reconcile it either — its
 *   `runtime_status` gate holds until the operator's `resetWhatsappPairing` clears
 *   the error. This is the only console path to that repair, which the dialog
 *   offers for exactly those two states. Firing resume here would redispatch the
 *   very session that just failed, which is what the task's gate exists to prevent.
 */
export function WhatsappConnectionAction({
  lifecycle: expectedLifecycle,
}: {
  lifecycle: WhatsappConnectionLifecycle;
}): React.ReactElement | null {
  const t = useMessagingWhatsappT();
  const { recordId, record } = useRecordChromeContext();
  const [dialogId, setDialogId] = React.useState<string | null>(null);
  const [resume] = useRecordChromeActionMutation("resume_whatsapp_pairing");
  const declaresIntent = expectedLifecycle !== "connected";

  const openConnection = React.useCallback((): void => {
    setDialogId(recordId);
    if (declaresIntent) void resume(recordId);
  }, [declaresIntent, recordId, resume]);

  // The button is what the lifecycle gates, not the dialog: `resume_whatsapp_pairing`
  // sets the channel CONNECTED on click (the lifecycle is the operator's declared
  // intent; whether the phone scan has landed is runtime state the session reports),
  // so the record's live push re-renders this with the button's gate already false.
  // Gating the dialog on it too would unmount the QR pane before it has anything to
  // report. The dialog closes when the user closes it.
  const showButton = record !== null && integrationLifecycle(record) === expectedLifecycle;
  if (!showButton && dialogId === null) return null;

  return (
    <>
      {showButton ? (
        <Button
          variant={declaresIntent ? "primary" : "secondary"}
          size="sm"
          onClick={openConnection}
        >
          <Glyph decorative name="link" />
          {t(BUTTON_LABEL_KEY[expectedLifecycle])}
        </Button>
      ) : null}
      <PairingDialog channelId={dialogId} onClose={() => setDialogId(null)} />
    </>
  );
}
