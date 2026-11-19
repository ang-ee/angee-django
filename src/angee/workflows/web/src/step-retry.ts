import { useMemo } from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  useActionOutcomeMutation, useAppRuntime, useRecordActionMutation, useUiT, type ActionDescriptor,
} from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import { RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** The one form an operator fills to accept a possible duplicate external effect. */
export function useDuplicateAcknowledgement(description: string) {
  const t = useWorkflowsT();
  const uiT = useUiT();
  const { widgets } = useAppRuntime();
  return useMemo(() => jsonSchemaActionArgs({
    type: "object", properties: { acknowledge: { type: "boolean", const: true, default: false,
      label: t("action.acknowledge"), description } },
    required: ["acknowledge"],
  }, widgets, { translate: uiT }), [description, t, uiT, widgets]);
}

/** Both execution views use the same retry and duplicate-effect acknowledgement. */
export function useStepRetryActions(inline = false): readonly ActionDescriptor[] {
  const t = useWorkflowsT();
  const [retry] = useRecordActionMutation<ActionFieldName>("retry_step", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const [retryDuplicate] = useActionOutcomeMutation<ActionFieldName>("retry_step_accepting_duplicate", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const acknowledgement = useDuplicateAcknowledgement(t("action.duplicateDescription"));
  return [{
    id: "retry", label: t(inline ? "timeline.retry" : "action.retry_step"), placement: inline ? "toolbar" : undefined, primary: !inline, run: retry,
    visibleWhen: (row) => row.can_retry === true && row.requires_duplicate_acknowledgement !== true,
    confirm: inline ? undefined : { title: t("action.retry_step"), body: t("action.retryDescription") },
  }, {
    id: "retry-duplicate", label: t("action.retry_step_accepting_duplicate"),
    primary: true, danger: true, args: acknowledgement,
    visibleWhen: (row) => row.can_retry === true && row.requires_duplicate_acknowledgement === true,
    submit: (_values, { record }, options) => typeof record?.id === "string" ? retryDuplicate(record.id, {}, options) : null,
  }];
}
