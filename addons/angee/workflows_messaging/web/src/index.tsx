import { defineBaseAddon } from "@angee/app";
import { Field, Group, optionToken } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";

export default defineBaseAddon({
  id: "workflows_messaging",
  containers: {
    [`${TRIGGER_MODEL}#sections`]: {
      "workflows_messaging.channel": {
        sequence: 20,
        content: <Group label="Message source">
          <Field name="channel" label="Channel" showWhen={(row) =>
            row.source_model === "messaging.Message" || optionToken(row.source) === "message_ingested"} />
        </Group>,
      },
    },
  },
});
