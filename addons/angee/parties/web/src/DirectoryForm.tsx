import * as React from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { usePartiesT } from "./i18n";

const MODEL = "parties.Directory";

function DirectoryForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const syncAction = useIntegrationSyncAction("sync_integration", t("directory.action.sync"));
  return (
    <Form {...props} resource={MODEL}>
      <Field name="display_name" title readOnly />
      <Field name="lifecycle" readOnly />
      <Field name="runtime_status" widget="colorDot" readOnly />
      <Field name="backend_class" readOnly />
      <Field name="config" readOnly />
      {IntegrationSyncFields({ label: t("directory.group.lastSync") })}
      {syncAction}
    </Form>
  );
}

export const directoryForm = registerForm(MODEL, DirectoryForm);
