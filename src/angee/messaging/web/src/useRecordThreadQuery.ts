import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery, type AuthoredVariables } from "@angee/refine";

import { READ_MODELS, RecordThreadDocument } from "./documents";

/** Record chatter refreshes through its parent's ledger feed even when the
 * private thread and messages have no generic change feed. */
export function useRecordThreadQuery(variables: AuthoredVariables<typeof RecordThreadDocument>) {
  const model = useModelMetadata(variables.modelLabel)?.resource.canonicalLabel ?? variables.modelLabel;
  return useAuthoredQuery(RecordThreadDocument, variables, {
    enabled: Boolean(model && variables.recordId),
    models: [...READ_MODELS, model],
    records: [{ model, id: String(variables.recordId) }],
  });
}
