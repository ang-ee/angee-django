import { useModelMetadata } from "@angee/metadata";
import { useAuthoredMutation, useAuthoredQuery, useSetAuthoredQueryData, type AuthoredVariables } from "@angee/refine";

import { READ_MODELS, RecordFollowingDocument, RecordThreadDocument, SetRecordFollowingDocument, type RecordThreadPayload } from "./documents";

/** Record chatter refreshes through its parent's ledger feed even when the
 * private thread and messages have no generic change feed. */
export function useRecordThreadQuery(variables: AuthoredVariables<typeof RecordThreadDocument>) {
  const model = useModelMetadata(variables.modelLabel)?.resource.canonicalLabel ?? variables.modelLabel;
  return useAuthoredQuery(RecordThreadDocument, { modelLabel: variables.modelLabel, recordId: variables.recordId }, {
    enabled: Boolean(model && variables.recordId),
    models: [...READ_MODELS, model],
    records: [{ model, id: String(variables.recordId) }],
  });
}

/** The bell observes only its parent and record-related thread changes. */
export function useRecordFollowingQuery(variables: AuthoredVariables<typeof RecordFollowingDocument>) {
  const model = useModelMetadata(variables.modelLabel)?.resource.canonicalLabel ?? variables.modelLabel;
  return useAuthoredQuery(RecordFollowingDocument, variables, {
    enabled: Boolean(model && variables.recordId),
    models: [model, "messaging.Thread"],
    records: [{ model, id: String(variables.recordId) }],
    relatedModels: ["messaging.Thread"],
  });
}

/** Adopt only returned thread facts; derived totals remain stale until reconciliation. */
export function useAcceptRecordThreadState(modelLabel: string, recordId: string) {
  const acceptQuery = useSetAuthoredQueryData();
  return (state: Partial<RecordThreadPayload>) => acceptQuery(RecordThreadDocument, { modelLabel, recordId }, (current) => {
    if (!current || current.record_thread.error_code) return current;
    return { ...current, record_thread: { ...current.record_thread, ...state } };
  }, { complete: false });
}

/** Both Follow surfaces share acceptance of the same canonical thread query. */
export function useRecordFollowing(modelLabel: string, recordId: string) {
  const model = useModelMetadata(modelLabel)?.resource.canonicalLabel ?? modelLabel;
  const acceptState = useAcceptRecordThreadState(modelLabel, recordId);
  const acceptQuery = useSetAuthoredQueryData();
  return useAuthoredMutation(SetRecordFollowingDocument, {
    records: [{ model, id: recordId, action: "update" }],
    errorFrom: (data) => data?.set_record_following,
    acceptResponse: async (data) => {
      const result = data?.set_record_following;
      if (!result) return;
      acceptQuery(RecordFollowingDocument, { modelLabel, recordId }, (current) => current
        ? { ...current, record_thread: { ...current.record_thread, is_following: result.is_following } }
        : current, { complete: false });
      return await acceptState({ follower_count: result.follower_count, is_following: result.is_following,
        followers: result.followers, self_follower: result.follower });
    },
  });
}
