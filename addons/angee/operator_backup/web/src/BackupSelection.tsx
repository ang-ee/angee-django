import { holdsPermission } from "@angee/metadata";
import { useEffect } from "react";
import * as v from "valibot";
import { ErrorBanner, LabeledDescriptorField, type FieldDescriptor, useRecordChromeContextMaybe, type WidgetRenderProps } from "@angee/ui";
import { BACKUP_GROUPS, BackupSelectionSchema, defaultBackupSelection, updateSelectionGroup } from "./selection";
import { BackupDiscovery } from "./BackupDiscovery";
import { useBackupT } from "./i18n";

/** Backup vocabulary over native descriptor controls; the enclosing FormView owns draft state. */
export function BackupSelectionField({ value, readOnly, disabled, onChange, onCommit, onValidityChange }: WidgetRenderProps) {
  const t = useBackupT();
  const chrome = useRecordChromeContextMaybe();
  const parsed = v.safeParse(BackupSelectionSchema, value ?? defaultBackupSelection());
  useEffect(() => onValidityChange?.(parsed.success), [onValidityChange, parsed.success]);
  if (!parsed.success) return <ErrorBanner description={t("invalidSelection")} />;
  const selection = parsed.output;
  return <div className="space-y-4">
    <p className="text-13 text-fg-muted">{t("localTarget")}</p>
    <p className="text-13 text-fg-muted">{t("captureContents")}</p>
    <p className="text-13 text-fg-muted">{t("selectionHint")}</p>
    {BACKUP_GROUPS.map((key) => {
      const group = selection[key];
      const field: FieldDescriptor = { name: key, label: t(key), widget: "object", objectTemplate: [
        { name: "mode", label: t("mode"), widget: "select", options: ["all", "selected", "none"].map((mode) => ({ value: mode, label: t(mode) })) },
        ...(group.mode === "selected" ? [{ name: "names", label: t("names"), widget: "list", itemTemplate: { name: "name", label: t("resourceName"), widget: "text" } }] : []),
        ...(group.mode !== "none" ? [{ name: "exclude", label: t("exclude"), widget: "list", itemTemplate: { name: "name", label: t("resourceName"), widget: "text" } }] : []),
        ...(key === "sources" ? [{ name: "policy", label: t("policy"), description: t("sourceHint"), widget: "select", options: [{ value: "bundle", label: t("bundle") }, { value: "fetch_published", label: t("fetchPublished") }] }] : []),
      ] };
      return <LabeledDescriptorField key={key} field={field} value={group} readOnly={readOnly} disabled={disabled} onCommit={onCommit}
        onChange={(next) => onChange?.(updateSelectionGroup(selection, key, next))} />;
    })}
    <BackupDiscovery id={chrome?.recordId} discoveryBlocked={chrome?.actionsBlocked || !holdsPermission(chrome?.record, "run")} selection={selection} readOnly={readOnly} disabled={disabled} onChange={onChange} />
  </div>;
}
