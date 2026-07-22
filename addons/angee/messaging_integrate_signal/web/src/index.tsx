import { defineChannelBridgeAddon } from "@angee/messaging";

import { ConnectSignalChannelAction } from "./ConnectSignalChannelAction";
import { enMessagingSignalMessages } from "./i18n";

export const SIGNAL_CONNECT_ACTION_ID = "messaging-integrate-signal.connect";
export const SIGNAL_PAIRING_ACTION_ID = "messaging-integrate-signal.pairing";
export const SIGNAL_BACKEND = "signal";

const messagingIntegrateSignal = defineChannelBridgeAddon({
  id: "messaging-integrate-signal",
  key: SIGNAL_BACKEND,
  sequence: 22,
  connectAction: <ConnectSignalChannelAction />,
  i18n: enMessagingSignalMessages,
  instructionKey: "channel.signal.scan",
});

export default messagingIntegrateSignal;
