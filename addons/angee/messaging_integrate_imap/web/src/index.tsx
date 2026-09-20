import { defineChannelPollBridgeAddon } from "@angee/messaging";

import { ConnectImapChannelAction } from "./ConnectImapChannelAction";
import { enMessagingImapMessages } from "./i18n";
import { ImportImapSampleAction } from "./ImportImapSampleAction";
import { PrepareImapNewMailAction } from "./PrepareImapNewMailAction";
import { UpdateImapCredentialAction } from "./UpdateImapCredentialAction";

const messagingIntegrateImap = defineChannelPollBridgeAddon({
  id: "messaging-integrate-imap",
  key: "imap",
  sequence: 10,
  connectAction: <ConnectImapChannelAction />,
  i18n: { messaging: enMessagingImapMessages },
  recordActions: [
    { id: "messaging-integrate-imap.credential", sequence: 20, content: <UpdateImapCredentialAction /> },
    { id: "messaging-integrate-imap.new-mail", sequence: 30, content: <PrepareImapNewMailAction /> },
    { id: "messaging-integrate-imap.sample", sequence: 40, content: <ImportImapSampleAction /> },
  ],
});

export default messagingIntegrateImap;
