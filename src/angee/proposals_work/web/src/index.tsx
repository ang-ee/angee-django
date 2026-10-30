import { defineBaseAddon } from "@angee/app";
import { holdsPermission } from "@angee/metadata";
import { Field, Group } from "@angee/ui";

import { enProposalsWorkMessages } from "./i18n";

const NAMESPACE = "proposals-work";

/** A label from this addon's bundle, resolved by the form through the runtime i18n. */
function labelKey(key: keyof typeof enProposalsWorkMessages) {
  return { namespace: NAMESPACE, key, fallback: enProposalsWorkMessages[key] };
}

export default defineBaseAddon({
  id: "proposals-work",
  i18n: { [NAMESPACE]: enProposalsWorkMessages },
  containers: {
    "proposals.Round#sections": {
      "proposals-work.questions": {
        sequence: 30,
        content: (
          <Group label={labelKey("round.group.questions")}>
            <Field name="permissions" hidden readOnly />
            <Field
              name="clarification_queue"
              label={labelKey("round.queue")}
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
