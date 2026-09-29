import * as v from "valibot";
import type { ActionOutcome } from "@angee/refine";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  actionOutcomeSubmitResult, ActionFormProvider, Button, DescriptorFieldList, DialogForm,
  ErrorBanner, useActionForm, useActionOutcomeMutation, type ResolverResult,
} from "@angee/ui";
import { RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";

type RunVerb = Extract<ActionFieldName, "cancel_workflow_run" | "reprocess_workflow_run" | "retry_step" | "retry_step_accepting_duplicate">;
export interface RunActionTarget { action: RunVerb; id: string }
type Acknowledgement = { acknowledge: boolean };

/** Mounted only for the page's selected action; backend facts own eligibility. */
export function RunActionDialog({ action, id, onClose, onReprocessed }: RunActionTarget & {
  onClose: () => void;
  onReprocessed: (outcome: ActionOutcome) => void;
}) {
  const t = useWorkflowsT();
  const [mutate] = useActionOutcomeMutation<ActionFieldName>(action, { dataProviderName: "console", invalidateModels: RUN_MODELS });
  const duplicate = action === "retry_step_accepting_duplicate";
  const label = {
    cancel_workflow_run: t("action.cancel_workflow_run"),
    reprocess_workflow_run: t("action.reprocess_workflow_run"),
    retry_step: t("action.retry_step"),
    retry_step_accepting_duplicate: t("action.retry_step_accepting_duplicate"),
  }[action];
  const schema = v.object({ acknowledge: v.pipe(v.boolean(), v.check((value) => !duplicate || value, t("action.acknowledgementRequired"))) });
  const form = useActionForm<Acknowledgement, ActionOutcome>({
    defaultValues: { acknowledge: false }, fieldNames: ["acknowledge"], toastSuccess: action !== "reprocess_workflow_run",
    resolver: (values): ResolverResult<Acknowledgement> => {
      const result = v.safeParse(schema, values);
      return result.success ? { values: result.output, errors: {} }
        : { values: {}, errors: { acknowledge: { type: result.issues[0].type, message: result.issues[0].message } } };
    },
    submit: async () => actionOutcomeSubmitResult(await mutate(id)),
    onSuccess: (_values, outcome) => {
      if (action === "reprocess_workflow_run") onReprocessed(outcome);
      onClose();
    },
  });
  const description = t(duplicate ? "action.duplicateDescription" : action === "cancel_workflow_run"
    ? "action.cancelDescription" : action === "reprocess_workflow_run" ? "action.reprocessDescription" : "action.retryDescription");
  return <ActionFormProvider {...form.form}>
    <DialogForm open onOpenChange={(open) => { if (!open && !form.submitting) onClose(); }}
      title={label} description={description}
      onSubmit={(event) => { event.preventDefault(); void form.run(); }}
      footer={<Button type="submit" disabled={form.submitting}>{label}</Button>}>
      {duplicate ? <DescriptorFieldList fields={[{ name: "acknowledge", label: t("action.acknowledge"), widget: "boolean" }]} readOnly={form.submitting} /> : null}
      <ErrorBanner description={form.formError} />
    </DialogForm>
  </ActionFormProvider>;
}
