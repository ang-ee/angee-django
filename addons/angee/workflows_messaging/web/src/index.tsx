import { defineBaseAddon } from "@angee/app";
import { Field, Group, optionToken } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";

import { enWorkflowsMessagingMessages } from "./i18n";

export default defineBaseAddon({
  id: "workflows_messaging",
  i18n: { workflows_messaging: enWorkflowsMessagingMessages },
  containers: {
    [`${TRIGGER_MODEL}#sections`]: {
      "workflows_messaging.channel": {
        sequence: 20,
        content: (
          <Group label={{ namespace: "workflows_messaging", key: "trigger.messageSource", fallback: enWorkflowsMessagingMessages["trigger.messageSource"] }}>
            <Field
              name="channel"
              label={{ namespace: "workflows_messaging", key: "trigger.channel", fallback: enWorkflowsMessagingMessages["trigger.channel"] }}
              showWhen={(row) => optionToken(row.source) === "message_ingested"}
            />
          </Group>
        ),
      },
    },
  },
});
