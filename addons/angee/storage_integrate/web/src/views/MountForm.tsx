import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";
import * as React from "react";

import { MOUNT_MODEL } from "../documents";
import { useStorageIntegrateT } from "../i18n";

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
