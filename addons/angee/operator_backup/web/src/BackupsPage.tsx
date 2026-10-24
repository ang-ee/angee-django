import { Column, List, ResourceList } from "@angee/ui";
import { backupForm } from "./forms";
import { useBackupT } from "./i18n";
import { BACKUP_MODEL } from "./resources";

export function BackupsPage() {
  const t = useBackupT();
  return <ResourceList resource={BACKUP_MODEL} form={backupForm} hideCreate placement="inline" routed>
    <List order={{ created_at: "DESC" }} emptyContent={t("backupEmpty")}>
      <Column field="display_name" header={t("backup")} />
      <Column field="plan" header={t("plan")} />
      <Column field="created_at" header={t("created")} />
      <Column field="file" header={t("archive")} />
    </List>
  </ResourceList>;
}
