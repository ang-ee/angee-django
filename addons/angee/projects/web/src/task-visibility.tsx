import type { Row } from "@angee/metadata";
import { extractActionOutcome, type DocumentVariables } from "@angee/refine";
import { FieldDescriptorControl, useAuthoredResourceMutation, useEnumOptions } from "@angee/ui";

import { SetTaskVisibilityDocument } from "./documents";
import { TASK_MODEL } from "./resources";

/** The task verb owns choices; the framework widget owns inline presentation. */
export function TaskVisibility({ record, disabled = false }: { record: Row; disabled?: boolean }) {
  const options = useEnumOptions(TASK_MODEL, "visibility", { casing: "upper" });
  const [change, state] = useAuthoredResourceMutation(SetTaskVisibilityDocument, {
    invalidateModels: [TASK_MODEL], shouldInvalidate: (data) => data?.set_task_visibility.ok === true,
  });
  return <FieldDescriptorControl value={record.visibility} field={{ name: "visibility", widget: "visibility", options,
    visibility: {
      disabled: disabled || state.fetching,
      allowedValues: Array.isArray(record.allowed_visibility) ? record.allowed_visibility.filter((value): value is string => typeof value === "string") : [],
      onSelect: async (value) => {
        if (typeof record.id !== "string" || typeof record.revision !== "number") return null;
        return extractActionOutcome(await change({ id: record.id, expected_revision: record.revision,
          visibility: value as DocumentVariables<typeof SetTaskVisibilityDocument>["visibility"],
        }), "set_task_visibility");
      },
    },
  }} />;
}
