import type { Row } from "@angee/metadata";
import { extractActionOutcome, type DocumentVariables } from "@angee/refine";
import { FieldDescriptorControl, useAuthoredResourceMutation, useEnumOptions } from "@angee/ui";

import { ANSWER_VISIBILITY } from "./documents";
import { ANSWER_MODEL } from "./resources";

/** Answer permissions and narrowing order are projected by Answer.set_visibility. */
export function AnswerVisibility({ record, disabled = false }: { record: Row; disabled?: boolean }) {
  const options = useEnumOptions(ANSWER_MODEL, "visibility", { casing: "upper" });
  const [change, state] = useAuthoredResourceMutation(ANSWER_VISIBILITY, {
    invalidateModels: [ANSWER_MODEL], shouldInvalidate: (data) => data?.set_proposal_answer_visibility.ok === true,
  });
  return <FieldDescriptorControl value={record.visibility} field={{ name: "visibility", widget: "visibility", options,
    visibility: {
      disabled: disabled || state.fetching,
      allowedValues: Array.isArray(record.allowed_visibility) ? record.allowed_visibility.filter((value): value is string => typeof value === "string") : [],
      onSelect: async (value) => {
        if (typeof record.id !== "string" || typeof record.revision !== "number") return null;
        return extractActionOutcome(await change({ answer: record.id, revision: record.revision,
          visibility: value as DocumentVariables<typeof ANSWER_VISIBILITY>["visibility"],
        }), "set_proposal_answer_visibility");
      },
    },
  }} />;
}
