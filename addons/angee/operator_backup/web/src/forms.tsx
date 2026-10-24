import * as v from "valibot";
import { Action, Field, FormView, Group, registerForm, relationValueId, useRecordAction, type RegisteredFormProps } from "@angee/ui";
import { BackupWizard } from "./BackupWizard";
import { useBackupCapture } from "./capture";
import { BackupArchive } from "./BackupArchive";
import { useBackupT } from "./i18n";
import { PLAN_MODEL, BACKUP_MODEL } from "./resources";
import { BackupSelectionSchema, defaultBackupSelection } from "./selection";

function BackupPlanForm(props: RegisteredFormProps) {
  const t = useBackupT();
  const capture = useBackupCapture();
  const run = useRecordAction(async (id, context) => {
    const selection = v.parse(BackupSelectionSchema, context.record?.selection);
    await capture.run(id, selection);
  });
  return <FormView {...props} eyebrow={t("plan")} toolbarStart={(context) => <BackupWizard context={context} capture={capture} />}>
    <Field name="name" label={t("name")} title />
    <Group columns={2}>
      <Field name="target_stack" label={t("target")} readOnly editOnly description={t("localTarget")} />
      <Field name="destination" label={t("destination")} widget="operator_backup.destination" readOnly editOnly />
    </Group>
    <Group columns={1}>
      <Field name="selection" label={t("selection")} widget="operator_backup.selection" defaultValue={defaultBackupSelection()} />
    </Group>
    <Action id="back-up-now" label={t("run")} placement="toolbar" primary permission="run" run={run}
      confirm={{ title: t("runConfirm"), body: <div className="space-y-3"><p>{t("runDescription")}</p><p>{t("captureContents")}</p></div> }} />
  </FormView>;
}
function BackupForm(props: RegisteredFormProps) {
  const t = useBackupT();
  return <FormView {...props} readOnly eyebrow={t("backup")} returning={["file"]}
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
