import type { ActionFieldName } from "@angee/gql/console/actions";
import { useRecordActionMutation, type ActionDescriptor } from "@angee/ui";
import { RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** A single confirmed cancellation verb for both execution surfaces. */
export function useRunCancelActions(): readonly ActionDescriptor[] {
  const t = useWorkflowsT();
  const [cancel] = useRecordActionMutation<ActionFieldName>("cancel_workflow_run", {
    dataProviderName: "console", invalidateModels: [...RUN_MODELS, "decisions.Decision", "decisions.DecisionRecord"],
  });
  return [{ id: "cancel", label: t("timeline.stop"), placement: "toolbar", danger: true,
    visibleWhen: (record) => record.can_cancel === true, run: cancel,
    confirm: { title: t("timeline.stop"), body: t("action.cancelDescription"), danger: true } }];
}
