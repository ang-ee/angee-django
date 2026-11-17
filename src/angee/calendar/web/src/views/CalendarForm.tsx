import * as React from "react";
import { Field, Form, Group, registerForm, type RegisteredFormProps } from "@angee/ui";

import { useCalendarT } from "../i18n";
import { CALENDAR_MODEL } from "../resources";

function CalendarForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useCalendarT();
  return (
    <Form {...props} resource={CALENDAR_MODEL}>
      <Field name="name" title />
      <Group label={t("form.details")} columns={2}>
        {/* Whose calendar it is is set once, on create; the server takes no update of it. */}
        <Field name="party" createOnly />
        <Field name="color" />
        <Field name="timezone" />
        <Field name="overlap_policy" />
      </Group>
      <Group label={t("form.description")} pane="description"><Field name="description" /></Group>
    </Form>
  );
}

export const calendarForm = registerForm(CALENDAR_MODEL, CalendarForm);
