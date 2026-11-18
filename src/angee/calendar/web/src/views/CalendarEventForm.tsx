import * as React from "react";
import { Field, Form, Group, dateFromValue, formatDateStorage, registerForm, type RegisteredFormProps } from "@angee/ui";

import { useCalendarT } from "../i18n";
import { EVENT_MODEL } from "../resources";
import { CALENDAR_PICKER_WIDGET, calendarPickerSelection, useWritableCalendarIds } from "./CalendarPicker";

// The picker renders the calendar itself, so the field names what it reads; `isReplica` reads `is_writable`.
const CALENDAR_SELECTION = [...calendarPickerSelection("calendar"), "calendar.is_writable"];

/** A day as a date picker writes it, with no time and no offset. */
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The day an all-day bound names. A picked day is itself; a stored bound is the instant of a midnight
 * in the event's effective zone (`effective_timezone`: its own, its calendar's or the project's), read
 * as its date there, so every viewer sees the event's own days whichever zone the browser is in.
 */
export function allDayBoundDay(value: unknown, zone: unknown): unknown {
  if (typeof value !== "string" || DAY.test(value)) return value;
  return formatDateStorage(dateFromValue(value), typeof zone === "string" && zone ? zone : undefined);
}

// An all-day event's bounds are days: the form shows each as the day it names and sends a picked day
// as a date, which the event places at midnight in its own zone, so a browser in another zone never
// sends its own midnight as the bound. A timed event's bounds are instants in the viewer's zone.
const asDay = (name: string) => (values: Readonly<Record<string, unknown>>) => ({
  name,
  ...(values.all_day === true ? {
    widget: "date",
    valueCodec: {
      toControl: (value: unknown) => allDayBoundDay(value, values.effective_timezone),
      fromControl: (value: unknown) => value,
    },
  } : {}),
});
const startAsDay = asDay("start");
const endAsDay = asDay("end");

/**
 * Toggling all day turns the bounds the viewer sees into whole days (the end is exclusive: the day
 * after the last one the event touches), and turning it off turns the days the event spans back
 * into the instants of the viewer's own midnights on them.
 */
export function allDayBounds(allDay: unknown, values: Readonly<Record<string, unknown>>): Record<string, unknown> | null {
  // Off, a bound is still the day it names; on, it is the instant the viewer sees.
  const read = (value: unknown) => dateFromValue(
    (allDay === true ? value : allDayBoundDay(value, values.effective_timezone)) as string | null | undefined,
  );
  const start = read(values.start);
  const end = read(values.end);
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
        <Field name="effective_timezone" readOnly hidden />
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
