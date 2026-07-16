import { ConditionalMutationButton, isConnectedOrPaused } from "@angee/integrate";
import * as React from "react";

import { useMessagingTelegramT } from "./i18n";

/**
 * Telegram specialization of the retained-session Disconnect verb.
 *
 * The live session stops and releases its account claim, while Telethon's device
 * store remains available for a later reconnect without another QR scan.
 */
export function TelegramDisconnectAction(): React.ReactElement | null {
  const t = useMessagingTelegramT();

  return (
    <ConditionalMutationButton
      field="disconnect_channel"
      label={t("channel.telegram.disconnect")}
      variant="danger"
      when={isConnectedOrPaused}
      confirm={{
        title: t("channel.telegram.disconnectConfirm.title"),
        body: t("channel.telegram.disconnectConfirm.body"),
        danger: true,
      }}
    />
  );
}
