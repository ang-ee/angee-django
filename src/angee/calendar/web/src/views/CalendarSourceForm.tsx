import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { Action, Field, Form, registerForm, useRecordActionMutation, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields } from "@angee/integrate";

import { useCalendarT } from "../i18n";
import { CALENDAR_MODEL, SOURCE_MODEL } from "../resources";

/**
 * A connected calendar, read-only: created through its connect verb. Its owner's
 * verbs (sync now, unsubscribe) show only to whoever the row's projected
 * permissions allow: `write` syncs, `delete` unsubscribes.
 */
function CalendarSourceForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useCalendarT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_calendar_source");
  const [unsubscribe] = useRecordActionMutation<ActionFieldName>("unsubscribe_calendar_source", {
    invalidateModels: [SOURCE_MODEL, CALENDAR_MODEL],
  });
  return (
    <Form {...props} resource={SOURCE_MODEL}>
      <Field name="display_name" title readOnly />
      <Field name="lifecycle" readOnly />
      <Field name="runtime_status" readOnly />
      <Field name="backend_class" readOnly />
      <Field name="poll_interval" readOnly />
      <Field name="config" readOnly />
      {IntegrationSyncFields({ label: t("connection.group.lastSync") })}
      <Action id="sync" label={t("connection.action.sync")} icon="refresh" permission="write" run={sync} />
      <Action
        id="unsubscribe"
        label={t("connection.action.unsubscribe")}
        icon="unlink"
        permission="delete"
        danger
        run={unsubscribe}
      />
    </Form>
  );
}

export const calendarSourceForm = registerForm(SOURCE_MODEL, CalendarSourceForm);
