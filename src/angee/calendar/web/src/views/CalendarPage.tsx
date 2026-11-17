import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useActionMutation, useAuthoredQuery } from "@angee/refine";
import {
  ResourceList,
  calendarWindowBounds,
  calendarWindowSource,
  errorMessage,
  useContainer,
  useResourceRecordHrefLookup,
  useRouteHref,
  useRuntimeAuth,
  useToast,
  type AnyCalendarWindowSource,
  type ListColumn,
  type Occurrence,
  type ResourceListCalendarSpec,
} from "@angee/ui";

import { CalendarListDocument, EventOccurrencesDocument } from "../documents.console";
import { useCalendarT } from "../i18n";
import { CALENDAR_MODEL, EVENT_MODEL } from "../resources";
import { OCCURRENCE_SOURCES, type CalendarSourceContribution } from "../sources";
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
 * The calendar page: a thin `ResourceList` declaration over the events resource.
 *
 * The windowed surface (`@angee/ui`) owns the fetch and window gating, period
 * navigation, quick-create, marker navigation, loading/error/retry and the source
 * toggles. The page declares one source per calendar the viewer reads (labelled, so
 * the toolbar toggles it: the calendar filter), one unlabelled source for busy time
 * on calendars the viewer only sees free/busy of, and the sources other addons
 * contribute to `calendar.events#occurrence-sources`. The calendar sources share one
 * document and its window variables, so they are one request; each keeps its own
 * calendar's occurrences.
 */
export function CalendarPage(): React.ReactElement {
  const t = useCalendarT();
  const toast = useToast();
  const routeHref = useRouteHref();
  const recordHref = useResourceRecordHrefLookup();
  // A contributed source reads the viewer (its own rows); signed out, it asks for none.
  const viewerId = useRuntimeAuth().user?.id ?? "";
  const contributions = useContainer<CalendarSourceContribution>(OCCURRENCE_SOURCES);
  const calendars = useAuthoredQuery(CalendarListDocument, undefined, { models: CALENDAR_MODELS });
  const [reschedule] = useActionMutation<ActionFieldName>("reschedule_event", { invalidateModels: EVENT_MODELS });

  const onReschedule = React.useCallback<NonNullable<ResourceListCalendarSpec["onReschedule"]>>(
    async (occurrence, start, end) => {
      try {
        const outcome = await reschedule(occurrence.event_sqid, {
          start: start.toISOString(),
          end: (end ?? start).toISOString(),
        });
        if (!outcome?.ok) throw new Error(outcome?.message || t("reschedule.failed"));
      } catch (error) {
        toast.danger({ title: t("reschedule.failed"), description: errorMessage(error, t("reschedule.failed")) });
        // Re-throw so the surface reverts its optimistic move or resize.
        throw error;
      }
    },
    [reschedule, toast, t],
  );

  const sources = React.useMemo<readonly AnyCalendarWindowSource[]>(() => {
    const listed = calendars.data?.calendars ?? [];
    const readable = new Set(listed.map((calendar) => calendar.id));
    // Redacted busy time shows as "Busy" and opens nothing; an event opens its row.
    const project = (row: OccurrenceRow): Occurrence => ({
      ...row,
      title: row.redacted ? t("occurrence.busy") : row.title,
      to: row.redacted ? undefined : routeHref("calendar.event", { id: row.event_sqid }),
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
        events((row) => row.calendar === calendar.id, { key: calendar.id, label: calendar.name }),
      ),
      events((row) => !readable.has(row.calendar)),
      ...contributions.map((entry) => entry.content(context)),
    ];
  }, [calendars.data, contributions, recordHref, routeHref, t, viewerId]);

  const calendar = React.useMemo<ResourceListCalendarSpec>(
    () => ({
      sources,
      onReschedule,
      // The surface opens routed create seeded with these start/end values.
      createDefaults: (start, end) => ({ start: start.toISOString(), end: end.toISOString() }),
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
