import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { type WidgetRenderProps } from "@angee/ui";
import { createElement } from "react";
import { lazyRouteComponent } from "@tanstack/react-router";
import { BackupDestination } from "./BackupDestination";
import { BackupSelectionField } from "./BackupSelection";
import { backupForm, backupPlanForm } from "./forms";
import { enBackupMessages } from "./i18n";
import { BACKUP_MODEL, PLAN_MODEL } from "./resources";

export default defineBaseAddon({
  id: "operator_backup",
  routes: [
    ...resourcePageRoutes("operator.backup-plans", "/operator/backup-plans", lazyRouteComponent(() => import("./BackupPlansPage"), "BackupPlansPage"), PLAN_MODEL),
    ...resourcePageRoutes("operator.backups", "/operator/backups", lazyRouteComponent(() => import("./BackupsPage"), "BackupsPage"), BACKUP_MODEL),
  ],
  menus: {
    "operator_backup.plans": { parent: "operator", label: enBackupMessages["plans"], icon: "archive", route: "operator.backup-plans", sequence: 70 },
    "operator_backup.backups": { parent: "operator", label: enBackupMessages["backups"], icon: "archive", route: "operator.backups", sequence: 80 },
  },
  forms: { [PLAN_MODEL]: backupPlanForm, [BACKUP_MODEL]: backupForm },
  widgets: { "operator_backup.destination": { read: BackupDestination }, "operator_backup.selection": { read: (props: WidgetRenderProps) => createElement(BackupSelectionField, { ...props, readOnly: true }), edit: BackupSelectionField } },
  i18n: { operator_backup: enBackupMessages },
});
