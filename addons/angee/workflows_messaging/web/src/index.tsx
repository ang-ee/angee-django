import { defineBaseAddon } from "@angee/app";
import { Field, Group, optionToken } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";

import { enWorkflowsMessagingMessages, useWorkflowsMessagingT } from "./i18n";

/** The trigger's message-source section: the channel a message-ingested trigger listens on. */
function ChannelSection() {
  const t = useWorkflowsMessagingT();
  return (
    <Group label={t("trigger.messageSource")}>
      <Field name="channel" label={t("trigger.channel")} showWhen={(row) =>
        row.source_model === "messaging.Message" || optionToken(row.source) === "message_ingested"} />
    </Group>
  );
}

export default defineBaseAddon({
  id: "workflows_messaging",
  i18n: { workflows_messaging: enWorkflowsMessagingMessages },
  containers: {
    [`${TRIGGER_MODEL}#sections`]: {
      "workflows_messaging.channel": {
        sequence: 20,
        content: <ChannelSection />,
      },
    },
  },
});
