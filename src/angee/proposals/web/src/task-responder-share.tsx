import { holdsPermission, type Row } from "@angee/metadata";
import { RecordActionBar, useActionResultRun, useRecordChromeActionOutcome, useRecordChromeContext } from "@angee/ui";
import type { ReactElement } from "react";

import { TASK_RESPONDER_SHARE } from "./documents";
import { useProposalsT } from "./i18n";

/** Responder sharing is a distinct proposal fact composed through record actions. */
export function TaskResponderShareAction(): ReactElement | null {
  const t = useProposalsT();
  const { record } = useRecordChromeContext();
  const [change] = useRecordChromeActionOutcome({
    document: TASK_RESPONDER_SHARE, resultField: "set_task_responder_share", idArgument: "task",
  });
  const settle = useActionResultRun();
  if (!record) return null;
  const shared = record.shared_with_responders === true;
  return <RecordActionBar record={record} actions={[{
    id: "task-responder-share",
    label: t(shared ? "task.action.unshare" : "task.action.share"),
    placement: "toolbar",
    visibleWhen: (row: Row) => Boolean((row.project as { source_proposal?: unknown } | null)?.source_proposal)
      && holdsPermission(row, shared ? "narrow" : "widen"),
    run: async (context) => {
      const id = context.record?.id;
      if (typeof id !== "string") return;
      await settle(() => change(id, { revision: context.record?.revision, shared: !shared }));
    },
  }]} />;
}
