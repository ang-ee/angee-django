import { defineChannelPollBridgeAddon } from "@angee/messaging";

import { ConnectImapChannelAction } from "./ConnectImapChannelAction";
import { enMessagingImapMessages } from "./i18n";
import { UpdateImapCredentialAction } from "./UpdateImapCredentialAction";

const messagingIntegrateImap = defineChannelPollBridgeAddon({
  id: "messaging-integrate-imap",
  key: "imap",
  sequence: 10,
  connectAction: <ConnectImapChannelAction />,
  i18n: { messaging: enMessagingImapMessages },
  recordActions: [
    { id: "messaging-integrate-imap.credential", sequence: 20, content: <UpdateImapCredentialAction /> },
  ],
});

export default messagingIntegrateImap;
