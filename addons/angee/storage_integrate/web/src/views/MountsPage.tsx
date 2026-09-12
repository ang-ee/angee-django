import { IntegrationSyncColumns, IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";
import {
  Column,
  Field,
  Form,
  List,
  ResourceList,
  registerForm,
  SlotOutlet,
  useSlot,
  type RegisteredFormProps,
} from "@angee/ui";
import * as React from "react";

import { MOUNT_MODEL } from "../documents";
import { useStorageIntegrateT } from "../i18n";
import { STORAGE_MOUNT_TOOLBAR_SLOT } from "../slots";

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

function MountForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const syncAction = useIntegrationSyncAction("sync_mount", t("mount.action.sync"));
  return (
      <Form {...props} resource={MOUNT_MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="mode" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="drive" readOnly />
        <Field name="lifecycle" readOnly />
        <Field name="runtime_status" widget="colorDot" readOnly />
        <Field name="config" widget="json" readOnly />
        {IntegrationSyncFields({ label: t("mount.group.sync") })}
        {syncAction}
      </Form>
  );
}

export const mountForm = registerForm(MOUNT_MODEL, MountForm);
