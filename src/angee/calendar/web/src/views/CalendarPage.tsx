import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useActionMutation, useAuthoredQuery } from "@angee/refine";
import {
  ResourceList,
  calendarWindowBounds,
  calendarWindowSource,
  dateFromValue,
  formatDateStorage,
  useActionResultRun,
  useContainer,
  useResourceRecordHrefLookup,
  useRuntimeAuth,
  type AnyCalendarWindowSource,
  type ListColumn,
  type Occurrence,
  type ResourceListCalendarSpec,
} from "@angee/ui";

import { CalendarListDocument, EventOccurrencesDocument } from "../documents.console";
import { useCalendarT } from "../i18n";
import { CALENDAR_MODEL, EVENT_MODEL } from "../resources";
import { OCCURRENCE_SOURCES, type OccurrenceSourceContribution } from "../sources";
import { calendarEventForm } from "./CalendarEventForm";

type OccurrenceRow = DocumentType<typeof EventOccurrencesDocument>["event_occurrences"][number];

// The plain events table the switcher shows in list view (calendar view renders the
// windowed surface instead). Headers derive from the field metadata.
const EVENT_COLUMNS: readonly ListColumn[] = [
  { field: "title" },
  { field: "calendar" },
  { field: "start" },
  { field: "end" },
  { field: "status" },
  { field: "location" },
];

const CALENDAR_MODELS = [CALENDAR_MODEL];
const EVENT_MODELS = [EVENT_MODEL];

/**
 * A bound as the event takes it from the grid. A timed bound is its instant. An all-day bound is the
 * day the viewer sees (FullCalendar's local date; the grid shows each event on its own days), sent as
 * a date that the event places at midnight in its own zone: a browser in another zone never sends its
 * own midnight as the bound, nor a day picked by the nearest midnight.
 */
function eventBound(moment: Date, allDay: boolean): string | null {
  return allDay ? formatDateStorage(moment) : moment.toISOString();
}

/**
 * The calendar page: a thin `ResourceList` declaration over the events resource.
 *
 * The windowed surface (`@angee/ui`) owns the fetch and window gating, period
 * navigation, quick-create, marker navigation, loading/error/retry and the source
 * toggles. The page declares:
 *
 * - one labelled source per listed calendar (its name and owning party), which the
 *   toolbar toggles: the calendar filter;
 * - one unlabelled source for occurrences of calendars the list does not name;
 * - the sources other addons contribute to `calendar.events#occurrence-sources`,
 *   each keyed by its container child id.
 *
 * The calendar sources share one document and its window variables, so they are one
 * request. Whether an occurrence is busy time only is the server's answer
 * (`redacted`); the list supplies labels only.
 */
export function CalendarPage(): React.ReactElement {
  const t = useCalendarT();
  const recordHref = useResourceRecordHrefLookup();
  // A contributed source reads the viewer (its own rows); signed out, it asks for none.
  const viewerId = useRuntimeAuth().user?.id ?? "";
  const contributions = useContainer<OccurrenceSourceContribution>(OCCURRENCE_SOURCES);
  const calendars = useAuthoredQuery(CalendarListDocument, undefined, { models: CALENDAR_MODELS });
  const [reschedule] = useActionMutation<ActionFieldName>("reschedule_event", { invalidateModels: EVENT_MODELS });
  const settle = useActionResultRun();

  const onReschedule = React.useCallback<NonNullable<ResourceListCalendarSpec["onReschedule"]>>(
    async (occurrence, start, end) => {
      const outcome = await settle(() => reschedule(occurrence.event_sqid, {
        start: eventBound(start, occurrence.all_day),
        end: eventBound(end ?? start, occurrence.all_day),
      }));
      // The settle owner has told the viewer; throwing makes the surface revert the move.
      if (!outcome?.ok) throw new Error(outcome?.message || t("reschedule.failed"));
    },
    [reschedule, settle, t],
  );

  const sources = React.useMemo<readonly AnyCalendarWindowSource[]>(() => {
    const listed = calendars.data?.calendars ?? [];
    const named = new Set(listed.map((calendar) => calendar.id));
    // Busy time shows as "Busy" and opens nothing; an event opens its row. An all-day occurrence is
    // the days its bounds fall on in its event's own zone, so every viewer sees it on those days.
    const day = (instant: string, zone: string) => formatDateStorage(dateFromValue(instant), zone) ?? instant;
    const project = (row: OccurrenceRow): Occurrence => ({
      ...row,
      ...(row.all_day
        ? { start: day(row.start, row.effective_timezone), end: day(row.end, row.effective_timezone) }
        : {}),
      title: row.redacted ? t("occurrence.busy") : row.title,
      to: row.redacted ? undefined : recordHref(EVENT_MODEL, row.event_sqid),
    });
    const events = (keep: (row: OccurrenceRow) => boolean, toggle?: { key: string; label: string }) =>
      calendarWindowSource({
        ...toggle,
        document: EventOccurrencesDocument,
        variables: (window) => calendarWindowBounds(window),
        select: (data) => data?.event_occurrences.filter(keep).map(project),
        models: EVENT_MODELS,
      });
    const context = { viewerId, recordHref };
    return [
      ...listed.map((calendar) =>
        events((row) => row.calendar === calendar.id, {
          key: calendar.id,
          label: calendar.party ? t("source.calendar", { calendar: calendar.name, party: calendar.party.display_name }) : calendar.name,
        }),
      ),
      events((row) => !named.has(row.calendar)),
      ...contributions.map((entry) => calendarWindowSource({ ...entry.content(context), key: entry.id })),
    ];
  }, [calendars.data, contributions, recordHref, t, viewerId]);

  const calendar = React.useMemo<ResourceListCalendarSpec>(
    () => ({
      sources,
      onReschedule,
      // The surface opens routed create seeded with the selection: a month-view day is an all-day
      // event on the days chosen, a timed selection its instants.
      createDefaults: (start, end, allDay) => ({
        start: eventBound(start, allDay), end: eventBound(end, allDay), all_day: allDay,
      }),
    }),
    [sources, onReschedule],
  );

  return (
    <ResourceList
      resource={EVENT_MODEL}
      form={calendarEventForm}
      placement="inline"
      routed
      defaultView="calendar"
      columns={EVENT_COLUMNS}
      calendar={calendar}
    />
  );
}
