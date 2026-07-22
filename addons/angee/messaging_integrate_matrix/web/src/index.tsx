import { defineChannelBridgeAddon } from "@angee/messaging";

import { ConnectMatrixChannelAction } from "./ConnectMatrixChannelAction";
import { enMessagingMatrixMessages } from "./i18n";

export const MATRIX_CONNECT_ACTION_ID = "messaging-integrate-matrix.connect";
export const MATRIX_PAIRING_ACTION_ID = "messaging-integrate-matrix.pairing";
export const MATRIX_BACKEND = "matrix";

const messagingIntegrateMatrix = defineChannelBridgeAddon({
  id: "messaging-integrate-matrix",
  key: MATRIX_BACKEND,
  sequence: 23,
  connectAction: <ConnectMatrixChannelAction />,
  i18n: enMessagingMatrixMessages,
  instructionKey: "channel.matrix.recovery",
});

export default messagingIntegrateMatrix;
