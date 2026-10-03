import { defineBaseAddon } from "@angee/app";
import { holdsPermission } from "@angee/metadata";
import { Field, Group, } from "@angee/ui";

import { enProposalsWorkMessages, useProposalsWorkT } from "./i18n";

function Label({ name }: { name: keyof typeof enProposalsWorkMessages }) {
  const t = useProposalsWorkT();
  return <>{t(name)}</>;
}

export default defineBaseAddon({
  id: "proposals-work",
  i18n: { "proposals-work": enProposalsWorkMessages },
  containers: {
    "proposals.Round#sections": {
      "proposals-work.questions": {
        sequence: 30,
        content: (
          <Group label={<Label name="round.group.questions" />}>
            <Field name="permissions" hidden readOnly />
            <Field
              name="clarification_queue"
              label={enProposalsWorkMessages["round.queue"]}
              resolve={(values) => ({
                name: "clarification_queue",
                readOnly: !holdsPermission(values, "write"),
              })}
            />
          </Group>
        ),
      },
    },
  },
});
