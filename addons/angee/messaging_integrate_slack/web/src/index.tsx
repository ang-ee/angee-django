import { defineChannelPollBridgeAddon } from "@angee/messaging";

import { ConnectSlackChannelAction } from "./ConnectSlackChannelAction";
import { enMessagingSlackMessages } from "./i18n";

const messagingIntegrateSlack = defineChannelPollBridgeAddon({
  id: "messaging-integrate-slack",
  key: "slack",
  sequence: 24,
  connectAction: <ConnectSlackChannelAction />,
  i18n: { messaging: enMessagingSlackMessages },
});

export default messagingIntegrateSlack;
