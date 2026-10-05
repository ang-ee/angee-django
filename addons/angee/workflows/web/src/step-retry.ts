import { useMemo } from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  useActionOutcomeMutation, useAppRuntime, useRecordActionMutation, useUiT, type ActionDescriptor,
} from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import { RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** Both execution views use the same retry and duplicate-effect acknowledgement. */
export function useStepRetryActions(inline = false): readonly ActionDescriptor[] {
  const t = useWorkflowsT();
  const uiT = useUiT();
  const { widgets } = useAppRuntime();
  const [retry] = useRecordActionMutation<ActionFieldName>("retry_step", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const [retryDuplicate] = useActionOutcomeMutation<ActionFieldName>("retry_step_accepting_duplicate", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const acknowledgement = useMemo(() => jsonSchemaActionArgs({
    type: "object", properties: { acknowledge: { type: "boolean", const: true, default: false,
      label: t("action.acknowledge"), description: t("action.duplicateDescription") } },
    required: ["acknowledge"],
  }, widgets, { translate: uiT }), [t, uiT, widgets]);
  return [{
    id: "retry", label: t(inline ? "timeline.retry" : "action.retry_step"), placement: "toolbar", primary: !inline, run: retry,
    visibleWhen: (row) => row.can_retry === true && row.requires_duplicate_acknowledgement !== true,
    confirm: inline ? undefined : { title: t("action.retry_step"), body: t("action.retryDescription") },
  }, {
    id: "retry-duplicate", label: t("action.retry_step_accepting_duplicate"), placement: "toolbar",
    primary: true, danger: true, args: acknowledgement,
    visibleWhen: (row) => row.can_retry === true && row.requires_duplicate_acknowledgement === true,
    submit: (_values, { record }) => typeof record?.id === "string" ? retryDuplicate(record.id) : null,
  }];
}
