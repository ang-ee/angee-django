import * as React from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { useCalendarT } from "../i18n";
import { SOURCE_MODEL } from "../resources";

function CalendarSourceForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useCalendarT();
  const syncAction = useIntegrationSyncAction("sync_integration", t("source.action.sync"));
  return (
    <Form {...props} resource={SOURCE_MODEL}>
      <Field name="display_name" title readOnly />
      <Field name="lifecycle" readOnly />
      <Field name="runtime_status" readOnly />
      <Field name="backend_class" readOnly />
      <Field name="poll_interval" readOnly />
      <Field name="config" readOnly />
      {IntegrationSyncFields({ label: t("source.group.lastSync") })}
      {syncAction}
    </Form>
  );
}

export const calendarSourceForm = registerForm(SOURCE_MODEL, CalendarSourceForm);
