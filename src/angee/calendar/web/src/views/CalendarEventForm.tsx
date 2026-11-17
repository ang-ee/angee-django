import * as React from "react";
import { Field, Form, Group, registerForm, type RegisteredFormProps } from "@angee/ui";

import { useCalendarT } from "../i18n";
import { EVENT_MODEL } from "../resources";

/** A read-only subscription's event is its source's replica: the saved record offers no edits. */
function isReplica(row: Record<string, unknown>): boolean {
  return (row.calendar as { is_writable?: boolean } | null | undefined)?.is_writable === false;
}

function CalendarEventForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useCalendarT();
  return (
    <Form {...props} resource={EVENT_MODEL} returning={["calendar.is_writable"]} readOnlyWhen={isReplica}>
      <Field name="title" title />
      <Group label={t("form.when")} columns={2}>
        <Field name="calendar" />
        <Field name="status" />
        <Field name="start" />
        <Field name="end" />
        <Field name="all_day" />
        <Field name="recurrence" />
        <Field name="timezone" />
      </Group>
      <Group label={t("form.details")} columns={2}>
        <Field name="location" />
        <Field name="conference_url" />
        <Field name="transparency" />
        <Field name="visibility" />
        <Field name="tags" />
      </Group>
      <Group label={t("form.description")} pane="description"><Field name="description" /></Group>
    </Form>
  );
}

export const calendarEventForm = registerForm(EVENT_MODEL, CalendarEventForm);
