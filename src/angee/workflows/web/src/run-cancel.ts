import type { ActionFieldName } from "@angee/gql/console/actions";
import { useRecordActionMutation, type ActionDescriptor } from "@angee/ui";
import { RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** A single confirmed cancellation verb for both execution surfaces; a record surface also refreshes its record, whose gates read the run. */
export function useRunCancelActions(recordModels: readonly string[] = []): readonly ActionDescriptor[] {
  const t = useWorkflowsT();
  const [cancel] = useRecordActionMutation<ActionFieldName>("cancel_workflow_run", {
    dataProviderName: "console", invalidateModels: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord", ...recordModels],
  });
  return [{ id: "cancel", label: t("timeline.stop"), placement: "toolbar", danger: true,
    visibleWhen: (record) => record.can_cancel === true, run: cancel,
    confirm: { title: t("timeline.stop"), body: t("action.cancelDescription"), danger: true } }];
}
