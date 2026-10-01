import { IntegrationSyncColumns } from "@angee/integrate";
import {
  Column,
  List,
  ResourceList,
  SlotOutlet,
  useSlot,
} from "@angee/ui";
import * as React from "react";

import { MOUNT_MODEL } from "../documents";
import { useStorageIntegrateT } from "../i18n";
import { STORAGE_MOUNT_TOOLBAR_SLOT } from "../slots";
import { mountForm } from "./MountForm";

/** Local and future vendor-backed external storage mounts. */
export function MountsPage(): React.ReactElement {
  const t = useStorageIntegrateT();
  const toolbarEntries = useSlot(STORAGE_MOUNT_TOOLBAR_SLOT);
  return (
    <ResourceList
      resource={MOUNT_MODEL}
      form={mountForm}
      placement="inline"
      routed
      hideCreate
      toolbarActions={<SlotOutlet entries={toolbarEntries} />}
    >
      <List resource={MOUNT_MODEL}>
        <Column field="display_name" header={t("mount.name")} />
        <Column field="mode" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        {IntegrationSyncColumns({ fields: ["sync_stage", "last_sync_completed_at"] })}
      </List>
    </ResourceList>
  );
}
