import { defineChannelPollBridgeAddon } from "@angee/messaging";

import { ConnectSlackChannelAction } from "./ConnectSlackChannelAction";
import { enMessagingSlackMessages } from "./i18n";

export const SLACK_BACKEND = "slack";

const messagingIntegrateSlack = defineChannelPollBridgeAddon({
  id: "messaging-integrate-slack",
  key: SLACK_BACKEND,
  sequence: 24,
  connectAction: <ConnectSlackChannelAction />,
  i18n: enMessagingSlackMessages,
});

export default messagingIntegrateSlack;
