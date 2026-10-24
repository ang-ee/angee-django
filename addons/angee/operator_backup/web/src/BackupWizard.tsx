import { useState } from "react";
import * as v from "valibot";
import { holdsPermission, rowPublicId } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import { Button, DialogForm, ErrorBanner, invalidFormSubmit, useActionForm, useWatch, type RecordToolbarContext } from "@angee/ui";
import { BackupDiscoveryView } from "./BackupDiscovery";
import type { BackupCapture } from "./capture";
import { BackupPlanPreviewDocument } from "./documents";
import { useBackupT } from "./i18n";
import { PLAN_MODEL } from "./resources";
import { BackupSelectionSchema, defaultBackupSelection, matchingPreviewSelection, type BackupSelection } from "./selection";

type ReviewDraft = { selection: BackupSelection; intent: "save" | "backup" };

/** The dialog owns a cancellable draft; the enclosing FormView owns every persisted save. */
export function BackupWizard({ context, capture }: { context: RecordToolbarContext; capture: BackupCapture }) {
  const t = useBackupT();
  const [open, setOpen] = useState(false);
  const parent = context.form;
  const writable = Boolean(parent && !parent.formReadOnly);
  const canRun = !context.recordId ? writable : holdsPermission(context.record, "run");
  const wizard = useActionForm<ReviewDraft, null>({
    defaultValues: { selection: defaultBackupSelection(), intent: "save" }, toastSuccess: false,
    submit: async ({ intent }) => {
      const accepted = matchingPreviewSelection(query.data?.preview_backup_plan?.selection, wizard.form.getValues("selection"));
      if (!accepted || query.isFetching || query.error) {
        return invalidFormSubmit({ fieldErrors: {}, formErrors: [t("previewStale")] });
      }
      let id = context.recordId;
      if (writable && parent) {
        parent.form.setValue("selection", accepted, { shouldDirty: true, shouldValidate: true });
        const saved = await parent.submitForm();
        id = saved ? rowPublicId(saved) ?? null : null;
        if (!id) return invalidFormSubmit({ fieldErrors: {}, formErrors: [t("reviewFields")] });
      } else if (intent === "save") return invalidFormSubmit({ fieldErrors: {}, formErrors: [t("reviewFields")] });
      if (intent === "backup" && (!id || !canRun || !await capture.run(id, accepted))) {
        return invalidFormSubmit({ fieldErrors: {}, formErrors: [t("captureFailed")] });
      }
      return { status: "ok", data: null };
    },
    onSuccess: () => setOpen(false),
  });
  const selection = useWatch({ control: wizard.form.control, name: "selection" });
  const query = useAuthoredQuery(BackupPlanPreviewDocument, { id: context.recordId, selection }, {
    dataProviderName: "console", enabled: open, staleTime: 0, models: [PLAN_MODEL], errorNotification: false,
  });
  const snapshot = matchingPreviewSelection(query.data?.preview_backup_plan?.selection, selection);
  const pending = wizard.submitting || capture.pending || Boolean(parent?.pending);
  const ready = Boolean(snapshot) && !query.isFetching && !query.error;
  const submit = (intent: ReviewDraft["intent"]) => {
    wizard.form.setValue("intent", intent);
    void wizard.run();
  };
  return <DialogForm open={open} onOpenChange={(next) => {
    if (pending) return;
    if (next) {
      const parsed = v.safeParse(BackupSelectionSchema, parent?.form.getValues("selection"));
      if (!parsed.success) return;
      wizard.form.reset({ selection: parsed.output, intent: "save" });
    }
    setOpen(next);
  }} title={t("discover")} description={t("previewHint")} size="lg"
    trigger={<Button type="button" disabled={!parent || !canRun || pending}>{t("discover")}</Button>}
    onSubmit={(event) => { event.preventDefault(); submit("save"); }}
    footer={<>
      <Button type="button" disabled={pending} onClick={() => setOpen(false)}>{t("cancel")}</Button>
      <Button type="button" disabled={!writable || !ready || pending} pending={wizard.submitting && wizard.form.getValues("intent") === "save"} onClick={() => submit("save")}>{t("savePlan")}</Button>
      <Button type="button" variant="primary" disabled={!canRun || !ready || pending} pending={wizard.submitting && wizard.form.getValues("intent") === "backup"} onClick={() => submit("backup")}>{t(writable ? "saveAndBackup" : "run")}</Button>
    </>}>
    <p className="text-13 text-fg-muted">{t("captureContents")}</p>
    <ErrorBanner description={wizard.formError} />
    <BackupDiscoveryView preview={query.data?.preview_backup_plan} pending={query.isFetching} error={query.error}
      selection={selection} readOnly={!writable || pending} onChange={(next) => wizard.form.setValue("selection", next)} />
  </DialogForm>;
}
