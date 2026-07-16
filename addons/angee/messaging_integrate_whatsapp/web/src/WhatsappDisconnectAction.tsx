import { ConditionalMutationButton, isConnectedOrPaused } from "@angee/integrate";
import * as React from "react";

import { useMessagingWhatsappT } from "./i18n";

/**
 * WhatsApp teardown-aware specialization of the shared Disconnect verb.
 *
 * Contributed under integrate's own Disconnect id against the
 * `messaging.Channel`/`whatsapp` impl key, so it replaces the inherited verb for a
 * WhatsApp channel only — every other channel (IMAP) keeps integrate's own.
 *
 * It gates on the same lifecycle set as the verb it replaces, and keeps a confirm:
 * messaging's `disconnect_channel` tears the live session down while retaining
 * reusable pairing material, so the vendor-worded confirmation remains here.
 */
export function WhatsappDisconnectAction(): React.ReactElement | null {
  const t = useMessagingWhatsappT();

  return (
    <ConditionalMutationButton
      field="disconnect_channel"
      label={t("channel.whatsapp.disconnect")}
      variant="danger"
      when={isConnectedOrPaused}
      confirm={{
        title: t("channel.whatsapp.disconnectConfirm.title"),
        body: t("channel.whatsapp.disconnectConfirm.body"),
        danger: true,
      }}
    />
  );
}
