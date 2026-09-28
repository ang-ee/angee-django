import { TASK_MODEL } from "@angee/projects";
import { extractActionOutcome, useAuthoredQuery } from "@angee/refine";
import { Button, ErrorBanner, useActionResultRun, useAuthoredResourceMutation, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { TASK_RESPONDER_AUDIENCE, TASK_RESPONDER_SHARE } from "./documents";
import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";
import { holdsPermission } from "./round-actions";

/** A task record's responder audience is one live column owned by proposals. */
export function TaskResponderShareAction(): ReactElement | null {
  const t = useProposalsT();
  const { recordId, dataProviderName } = useRecordChromeContext();
  const read = useAuthoredQuery(TASK_RESPONDER_AUDIENCE, { id: recordId }, {
    dataProviderName, models: [TASK_MODEL, PROPOSAL_MODEL, ROUND_MODEL],
  });
  const [share, state] = useAuthoredResourceMutation(TASK_RESPONDER_SHARE, {
    dataProviderName, invalidateModels: [TASK_MODEL],
  });
  const settle = useActionResultRun();
  const task = read.data?.project_tasks_by_pk;
  if (read.error) return <ErrorBanner title={t("task.audience.failed")} description={read.error.message} />;
  if (!task?.project?.source_proposal || !holdsPermission(task, task.shared_with_responders ? "narrow" : "widen")) return null;
  return (
    <Button type="button" size="sm" variant="secondary" disabled={state.fetching} onClick={() => {
      void settle(async () => extractActionOutcome(await share({
        task: task.id, revision: task.revision, shared: !task.shared_with_responders,
      }), "set_task_responder_share"));
    }}>
      {t(task.shared_with_responders ? "answer.action.unshare" : "answer.action.share")}
    </Button>
  );
}
