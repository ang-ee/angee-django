import { extractActionOutcome } from "@angee/refine";
import { useAuthoredResourceMutation, type ActionDescriptor } from "@angee/ui";

import { ANSWER_SHARE, ANSWER_VISIBILITY } from "./documents";
import { useProposalsT } from "./i18n";
import { ANSWER_MODEL } from "./resources";
import { holdsPermission, recordRevision } from "./round-actions";

/** Answer audience verbs preserve the server's narrowing and revision rules. */
export function useAnswerActions(): readonly ActionDescriptor[] {
  const t = useProposalsT();
  const options = { invalidateModels: [ANSWER_MODEL] };
  const [visibility] = useAuthoredResourceMutation(ANSWER_VISIBILITY, options);
  const [share] = useAuthoredResourceMutation(ANSWER_SHARE, options);
  return [
    {
      id: "answer-visibility", label: t("answer.action.visibility"),
      args: [{ name: "visibility", label: t("answer.action.visibility"), widget: "select", options: [
        { value: "round", label: t("answer.visibility.round") },
        { value: "responder", label: t("answer.visibility.responder") },
        { value: "sealed", label: t("answer.visibility.sealed") },
      ] }],
      submit: async (values, context) => {
        if (typeof context.record?.id !== "string" || typeof values.visibility !== "string") return;
        return extractActionOutcome(await visibility({
          answer: context.record.id, revision: recordRevision(context.record), visibility: values.visibility,
        }), "set_proposal_answer_visibility");
      },
      visibleWhen: (record) => holdsPermission(record, "narrow"),
    },
    ...([true, false] as const).map((shared): ActionDescriptor => ({
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
    })),
  ];
}
