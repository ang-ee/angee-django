import { Column, List, ResourceList, useRouteHref, type StringIdRow } from "@angee/ui";
import { RecordTimeline } from "@angee/workflows";
import { backupForm } from "./forms";
import { useBackupT } from "./i18n";
import { BACKUP_MODEL } from "./resources";

export function BackupsPage() { return <BackupsList />; }
export function BackupsList({ planId }: { planId?: string }) {
  const t = useBackupT();
  const href = useRouteHref();
  return <ResourceList<StringIdRow> resource={BACKUP_MODEL} form={backupForm} hideCreate placement="inline" routed={!planId} scope={planId ? "local" : undefined}
    baseFilter={planId ? { plan: { exact: planId } } : undefined}
    rowHref={planId ? (row) => href("operator.backups.record", { id: row.id }) : undefined}
    recordTabs={[{ id: "activity", label: t("execution"), render: ({ recordId }) => <RecordTimeline record={{ model: BACKUP_MODEL, id: recordId }} /> }]}>
    <List order={{ created_at: "DESC" }} emptyContent={t("backupEmpty")}>
      <Column field="display_name" header={t("backup")} />
      <Column field="plan" header={t("plan")} />
      <Column field="created_at" header={t("created")} />
      <Column field="file" header={t("archive")} />
    </List>
  </ResourceList>;
}
