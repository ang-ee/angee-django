import { useEffect } from "react";
import * as v from "valibot";
import { ErrorBanner, LabeledDescriptorField, Tabs, type FieldDescriptor, type WidgetRenderProps } from "@angee/ui";
import { BACKUP_GROUPS, BackupSelectionSchema, defaultBackupSelection, updateSelectionGroup } from "./selection";
import { useBackupT } from "./i18n";

/** Backup vocabulary over native descriptor controls; the enclosing FormView owns draft state. */
export function BackupSelectionField({ value, readOnly, disabled, onChange, onCommit, onValidityChange }: WidgetRenderProps) {
  const t = useBackupT();
  const parsed = v.safeParse(BackupSelectionSchema, value ?? defaultBackupSelection());
  useEffect(() => onValidityChange?.(parsed.success), [onValidityChange, parsed.success]);
  if (!parsed.success) return <ErrorBanner description={t("invalidSelection")} />;
  const selection = parsed.output;
  return <div className="space-y-4">
    <p className="text-13 text-fg-muted">{t("selectionHint")}</p>
    <Tabs defaultValue="databases">
    <Tabs.List className="overflow-x-auto" aria-label={t("selection")}>{BACKUP_GROUPS.map((key) => <Tabs.Tab key={key} value={key}>{t(key)}</Tabs.Tab>)}</Tabs.List>
    {BACKUP_GROUPS.map((key) => {
      const group = selection[key];
      const field: FieldDescriptor = { name: key, label: t(key), widget: "object", objectTemplate: [
        { name: "mode", label: t("mode"), widget: "select", options: ["all", "selected", "none"].map((mode) => ({ value: mode, label: t(mode) })) },
        ...(group.mode === "selected" ? [{ name: "names", label: t("names"), widget: "list", itemTemplate: { name: "name", label: t("resourceName"), widget: "text" } }] : []),
        ...(group.mode !== "none" ? [{ name: "exclude", label: t("exclude"), widget: "list", itemTemplate: { name: "name", label: t("resourceName"), widget: "text" } }] : []),
        ...(key === "sources" ? [{ name: "policy", label: t("policy"), description: t("sourceHint"), widget: "select", options: [{ value: "bundle", label: t("bundle") }, { value: "fetch_published", label: t("fetchPublished") }] }] : []),
      ] };
      return <Tabs.Panel key={key} value={key} keepMounted><LabeledDescriptorField field={field} value={group} readOnly={readOnly} disabled={disabled} onCommit={onCommit}
        onChange={(next) => onChange?.(updateSelectionGroup(selection, key, next))} /></Tabs.Panel>;
    })}
    </Tabs>
  </div>;
}
