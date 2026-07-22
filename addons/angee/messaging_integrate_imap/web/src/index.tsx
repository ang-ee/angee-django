import { defineChannelPollBridgeAddon } from "@angee/messaging";

import { ConnectImapChannelAction } from "./ConnectImapChannelAction";
import { enMessagingImapMessages } from "./i18n";

const messagingIntegrateImap = defineChannelPollBridgeAddon({
  id: "messaging-integrate-imap",
  key: "imap",
  sequence: 10,
  connectAction: <ConnectImapChannelAction />,
  i18n: enMessagingImapMessages,
});

export default messagingIntegrateImap;
