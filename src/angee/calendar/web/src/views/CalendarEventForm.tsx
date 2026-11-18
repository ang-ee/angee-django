import * as React from "react";
import { Field, Form, Group, dateFromValue, formatDateStorage, registerForm, type RegisteredFormProps } from "@angee/ui";

import { useCalendarT } from "../i18n";
import { EVENT_MODEL } from "../resources";
import { CALENDAR_PICKER_WIDGET, calendarPickerSelection, useWritableCalendarIds } from "./CalendarPicker";

// The picker renders the calendar itself, so the field names what it reads; `isReplica` reads `is_writable`.
const CALENDAR_SELECTION = [...calendarPickerSelection("calendar"), "calendar.is_writable"];

// An all-day event's bounds are days. The form picks them as days and sends them as dates; the
// event places each at midnight in its own zone (its own, its calendar's or the project's), which
// only the server knows, so a browser in another zone never sends its own midnight as the bound.
const startAsDay = (values: Readonly<Record<string, unknown>>) => ({ name: "start", ...(values.all_day === true ? { widget: "date" } : {}) });
const endAsDay = (values: Readonly<Record<string, unknown>>) => ({ name: "end", ...(values.all_day === true ? { widget: "date" } : {}) });

/**
 * Toggling all day turns the bounds the viewer sees into whole days (the end is exclusive: the day
 * after the last one the event touches), and turning it off turns those days back into the instants
 * of their local midnights.
 */
export function allDayBounds(allDay: unknown, values: Readonly<Record<string, unknown>>): Record<string, unknown> | null {
  const start = dateFromValue(values.start as string | null | undefined);
  const end = dateFromValue(values.end as string | null | undefined);
  if (!start || !end) return null;
  if (allDay !== true) return { start: start.toISOString(), end: end.toISOString() };
  const day = (moment: Date, after = 0) => new Date(moment.getFullYear(), moment.getMonth(), moment.getDate() + after);
  const first = day(start);
  const touched = day(end).getTime() < end.getTime() ? day(end, 1) : day(end);
  return { start: formatDateStorage(first), end: formatDateStorage(touched > first ? touched : day(first, 1)) };
}

/** A read-only subscription's event is its source's replica: the saved record offers no edits. */
function isReplica(row: Record<string, unknown>): boolean {
  return (row.calendar as { is_writable?: boolean } | null | undefined)?.is_writable === false;
}

function CalendarEventForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useCalendarT();
  // One writable calendar is the obvious choice; with none or several the viewer chooses.
  const writable = useWritableCalendarIds();
  const soleCalendar = writable?.length === 1 ? writable[0] : undefined;
  return (
    <Form {...props} resource={EVENT_MODEL} readOnlyWhen={isReplica}>
      <Field name="title" title />
      <Group label={t("form.when")} columns={2}>
        <Field name="calendar" widget={CALENDAR_PICKER_WIDGET} selectionPaths={CALENDAR_SELECTION} required
          defaultValue={soleCalendar} />
        <Field name="status" />
        <Field name="start" resolve={startAsDay} />
        <Field name="end" resolve={endAsDay} />
        <Field name="all_day" prefill={allDayBounds} />
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
