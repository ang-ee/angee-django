import { Column, List, ResourceList } from "@angee/ui";
import { useBackupT } from "./i18n";
import { PLAN_MODEL } from "./resources";
import { backupPlanForm } from "./forms";

export function BackupPlansPage() {
  const t = useBackupT();
  return <ResourceList resource={PLAN_MODEL} form={backupPlanForm} placement="inline" routed>
    <List order={{ name: "ASC" }} emptyContent={t("planEmpty")}>
      <Column field="name" header={t("name")} />
      <Column field="target_stack" header={t("target")} />
      <Column field="destination" header={t("destination")} />
    </List>
  </ResourceList>;
}
