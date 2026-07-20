import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action,
  Column,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useRecordActionMutation,
} from "@angee/ui";
import * as React from "react";

import { ConnectLocalFolderAction } from "../ConnectLocalFolderAction";
import { MOUNT_MODEL } from "../documents";
import { useStorageIntegrateT } from "../i18n";

/** Local and future vendor-backed external storage mounts. */
export function MountsPage(): React.ReactElement {
  const t = useStorageIntegrateT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_mount");
  return (
    <ResourceList
      resource={MOUNT_MODEL}
      placement="inline"
      routed
      hideCreate
      toolbarActions={<ConnectLocalFolderAction />}
    >
      <List resource={MOUNT_MODEL}>
        <Column field="display_name" header={t("mount.name")} />
        <Column field="mode" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="sync_stage" />
        <Column field="last_sync_completed_at" />
      </List>
      <Form resource={MOUNT_MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="mode" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="drive" readOnly />
        <Field name="lifecycle" readOnly />
        <Field name="runtime_status" readOnly />
        <Field name="config" widget="json" readOnly />
        <Group label={t("mount.group.sync")} columns={2}>
          <Field name="is_syncing" readOnly />
          <Field name="sync_stage" readOnly />
          <Field name="sync_error" readOnly />
          <Field name="sync_progress" widget="json" readOnly />
          <Field name="last_sync_summary" widget="json" readOnly />
          <Field name="last_sync_status" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Action
          id="sync"
          label={t("mount.action.sync")}
          icon="refresh"
          run={sync}
        />
      </Form>
    </ResourceList>
  );
}
