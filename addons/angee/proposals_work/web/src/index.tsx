import { defineBaseAddon } from "@angee/app";
import { Field, Group, formViewSectionsSlot } from "@angee/ui";

import { enProposalsWorkMessages, useProposalsWorkT } from "./i18n";

function Label({ name }: { name: keyof typeof enProposalsWorkMessages }) {
  const t = useProposalsWorkT();
  return <>{t(name)}</>;
}

export default defineBaseAddon({
  id: "proposals-work",
  i18n: { "proposals-work": enProposalsWorkMessages },
  slots: [{
    ...formViewSectionsSlot("proposals.Round"),
    id: "proposals-work.questions",
    sequence: 30,
    content: (
      <Group label={<Label name="round.group.questions" />}>
        <Field name="permissions" hidden readOnly />
        <Field
          name="clarification_queue"
          label={enProposalsWorkMessages["round.queue"]}
          resolve={(values) => ({
            name: "clarification_queue",
            readOnly: !Array.isArray(values.permissions) || !values.permissions.includes("write"),
          })}
        />
      </Group>
    ),
  }],
});
