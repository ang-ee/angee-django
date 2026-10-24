import { useRef } from "react";
import { rowPublicId } from "@angee/metadata";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { Action, createClientKey, Field, FormView, Group, registerForm, relationValueId, useRecordActionMutation, type RegisteredFormProps } from "@angee/ui";
import { BackupArchive } from "./BackupArchive";
import { useBackupT } from "./i18n";
import { PLAN_MODEL, BACKUP_MODEL } from "./resources";
import { defaultBackupSelection } from "./selection";

function BackupPlanForm(props: RegisteredFormProps) {
  const t = useBackupT();
  const requestKeys = useRef(new Map<string, string>());
  const [run] = useRecordActionMutation<ActionFieldName>("run_backup_plan", {
    dataProviderName: "console", invalidateModels: [PLAN_MODEL, BACKUP_MODEL, "workflows.WorkflowRun"], linkTo: "workflows.WorkflowRun",
    actionArguments: (id) => {
      const key = requestKeys.current.get(id) ?? createClientKey("backup");
      requestKeys.current.set(id, key);
      return { request_key: key };
    },
    afterSuccess: (context) => {
      const id = rowPublicId(context.record);
      if (id) requestKeys.current.delete(id);
    },
  });
  return <FormView {...props} eyebrow={t("plan")}>
    <Field name="name" label={t("name")} title />
    <Group columns={2}>
      <Field name="target_stack" label={t("target")} readOnly editOnly description={t("localTarget")} />
      <Field name="destination" label={t("destination")} widget="operator_backup.destination" readOnly editOnly />
    </Group>
    <Field name="selection" label={t("selection")} widget="operator_backup.selection" defaultValue={defaultBackupSelection()} />
    <Action id="back-up-now" label={t("run")} placement="toolbar" primary permission="run" run={run}
      confirm={{ title: t("runConfirm"), body: <div className="space-y-3"><p>{t("runDescription")}</p><p>{t("captureContents")}</p></div> }} />
  </FormView>;
}
function BackupForm(props: RegisteredFormProps) {
  const t = useBackupT();
  return <FormView {...props} readOnly eyebrow={t("backup")} returning={["file"]} deleteVisibleWhen={() => false}
    headerExtras={({ record }) => <BackupArchive id={relationValueId(record?.file) || undefined} />}>
    <Field name="display_name" title />
    <Group columns={2}>
      <Field name="plan" label={t("plan")} />
      <Field name="execution" label={t("execution")} />
      <Field name="file" label={t("archive")} />
      <Field name="created_at" label={t("created")} />
    </Group>
    <Field name="manifest" label={t("manifest")} widget="json" />
  </FormView>;
}
export const backupPlanForm = registerForm(PLAN_MODEL, BackupPlanForm);
export const backupForm = registerForm(BACKUP_MODEL, BackupForm);
