import { CHANNEL_MODEL, PairingDialog } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import { Button, Glyph, errorMessage, useToast } from "@angee/ui";
import * as React from "react";

import { ConnectSignalChannel } from "./documents";
import { useMessagingSignalT } from "./i18n";

/** One-click Signal channel creation followed by shared linked-device pairing. */
export function ConnectSignalChannelAction(): React.ReactElement {
  const t = useMessagingSignalT();
  const toast = useToast();
  const [pairingChannelId, setPairingChannelId] = React.useState<string | null>(null);
  const [connect, connectState] = useAuthoredMutation(ConnectSignalChannel, {
    invalidateModels: [CHANNEL_MODEL],
  });

  const start = async (): Promise<void> => {
    try {
      const data = await connect({});
      const id = data?.connect_signal_channel?.id;
      if (id) setPairingChannelId(String(id));
    } catch (cause) {
      toast.danger({ title: errorMessage(cause, t("channel.signal.error")) });
    }
  };

  return (
    <>
      <Button
        variant="primary"
        size="sm"
        disabled={connectState.fetching}
        loading={connectState.fetching}
        loadingText={t("channel.signal.connecting")}
        onClick={() => void start()}
      >
        <Glyph decorative name="plus" />
        {t("channel.signal.button")}
      </Button>
      <PairingDialog
        channelId={pairingChannelId}
        instruction={t("channel.signal.scan")}
        onClose={() => setPairingChannelId(null)}
      />
    </>
  );
}
