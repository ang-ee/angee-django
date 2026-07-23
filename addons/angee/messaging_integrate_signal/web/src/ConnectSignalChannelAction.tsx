import { usePairingConnect } from "@angee/messaging";
import { Button, Glyph, errorMessage, useToast } from "@angee/ui";
import * as React from "react";

import { ConnectSignalChannel } from "./documents";
import { useMessagingSignalT } from "./i18n";

/** One-click Signal channel creation followed by shared linked-device pairing. */
export function ConnectSignalChannelAction(): React.ReactElement {
  const t = useMessagingSignalT();
  const toast = useToast();
  const { connect, connectState, pairingDialog } = usePairingConnect(
    ConnectSignalChannel,
    "connect_signal_channel",
    t("channel.signal.scan"),
  );

  const start = async (): Promise<void> => {
    try {
      await connect({});
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
      {pairingDialog}
    </>
  );
}
