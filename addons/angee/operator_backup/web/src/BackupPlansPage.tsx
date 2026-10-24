import { Column, List, ResourceList } from "@angee/ui";
import { RecordTimeline } from "@angee/workflows";
import { BackupsList } from "./BackupsPage";
import { useBackupT } from "./i18n";
import { PLAN_MODEL } from "./resources";
import { backupPlanForm } from "./forms";

export function BackupPlansPage() {
  const t = useBackupT();
  return <ResourceList resource={PLAN_MODEL} form={backupPlanForm} placement="inline" routed recordTabs={[
    { id: "backups", label: t("captures"), render: ({ recordId }) => <BackupsList planId={recordId} /> },
    { id: "activity", label: t("execution"), render: ({ recordId }) => <RecordTimeline record={{ model: PLAN_MODEL, id: recordId }} /> },
  ]}>
    <List order={{ name: "ASC" }} emptyContent={t("planEmpty")}>
      <Column field="name" header={t("name")} />
      <Column field="target_stack" header={t("target")} />
      <Column field="destination" header={t("destination")} />
    </List>
  </ResourceList>;
}
