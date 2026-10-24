import { useRef } from "react";
import { extractActionOutcome, useAuthoredMutation } from "@angee/refine";
import { createClientKey, useActionResultRun } from "@angee/ui";
import { BackupRunDocument } from "./documents";
import { BACKUP_MODEL, PLAN_MODEL } from "./resources";
import { matchingPreviewSelection, type BackupSelection } from "./selection";

/** An identical reviewed capture retains its request key across ambiguous retries. */
export function useBackupCapture() {
  const keys = useRef(new Map<string, { key: string; selection: BackupSelection }>());
  const [mutate, state] = useAuthoredMutation(BackupRunDocument, {
    dataProviderName: "console", invalidateModels: [PLAN_MODEL, BACKUP_MODEL, "workflows.WorkflowRun"], errorNotification: false,
  });
  const settle = useActionResultRun({ linkTo: "workflows.WorkflowRun" });
  return {
    pending: state.fetching,
    run: async (id: string, selection: BackupSelection): Promise<boolean> => {
      const previous = keys.current.get(id);
      const requestKey = previous && matchingPreviewSelection(previous.selection, selection) ? previous.key : createClientKey("backup");
      keys.current.set(id, { key: requestKey, selection });
      const result = await settle(async () => extractActionOutcome(await mutate({ id, requestKey, expectedSelection: selection }), "run_backup_plan"));
      if (result?.ok) keys.current.delete(id);
      return result?.ok === true;
    },
  };
}
export type BackupCapture = ReturnType<typeof useBackupCapture>;
