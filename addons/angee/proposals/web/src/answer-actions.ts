import { extractActionOutcome } from "@angee/refine";
import { useAuthoredResourceMutation, type ActionDescriptor } from "@angee/ui";

import { ANSWER_SHARE } from "./documents";
import { useProposalsT } from "./i18n";
import { ANSWER_MODEL } from "./resources";
import { holdsPermission, recordRevision } from "./round-actions";

/** Answer audience verbs preserve the server's narrowing and revision rules. */
export function useAnswerActions(): readonly ActionDescriptor[] {
  const t = useProposalsT();
  const options = { invalidateModels: [ANSWER_MODEL] };
  const [share] = useAuthoredResourceMutation(ANSWER_SHARE, options);
  return ([true, false] as const).map((shared): ActionDescriptor => ({
    id: shared ? "share-answer" : "unshare-answer",
    label: t(shared ? "answer.action.share" : "answer.action.unshare"),
    args: [],
    submit: async (_values, context) => {
      if (typeof context.record?.id !== "string") return;
      return extractActionOutcome(await share({
        answer: context.record.id, revision: recordRevision(context.record), shared,
      }), "set_proposal_answer_responder_share");
    },
    visibleWhen: (record) => holdsPermission(record, shared ? "manage" : "narrow") && record.shared_with_responders !== shared,
  }));
}
