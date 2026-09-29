import { defineBaseAddon } from "@angee/app";
import { Field, Group, formViewSectionsSlot, optionToken } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";

export default defineBaseAddon({
  id: "workflows_messaging",
  slots: [{
    ...formViewSectionsSlot(TRIGGER_MODEL),
    id: "workflows_messaging.channel",
    sequence: 20,
    content: <Group label="Message source">
      <Field name="channel" label="Channel" showWhen={(row) => optionToken(row.source) === "message_ingested"} />
    </Group>,
  }],
});
