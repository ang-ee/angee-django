import * as React from "react";
import type { CalendarSourceContribution } from "@angee/calendar";
import { calendarWindowSource, formatDateStorage, type Occurrence, type ResourceRecordHrefLookup } from "@angee/ui";

import { CalendarDueTasksDocument, type CalendarDueTaskRow } from "./documents";
import { useProjectsT } from "./i18n";
import { TASK_MODEL } from "./resources";

/** A local `yyyy-MM-dd`: the `Date`-typed due bounds never cross the UTC boundary. */
function localDay(date: Date): string {
  return formatDateStorage(date) ?? "";
}

/**
 * Map one due task onto the occurrence wire shape: an all-day, read-only marker
 * that opens the record the task is about (its subject), or the task itself. A task
 * with no due date places no marker.
 */
export function dueTaskOccurrences(row: CalendarDueTaskRow, recordHref: ResourceRecordHrefLookup): Occurrence[] {
  if (!row.due_date) return [];
  return [
    {
      occurrence_id: `projects.task:${row.id}`,
      // A task marker has no calendar event; navigation rides `to`.
      event_sqid: "",
      title: row.title || row.subject?.label || "",
      // A bare date string reads as a zone-free all-day marker.
      start: row.due_date,
      end: row.due_date,
      all_day: true,
      editable: false,
      to: row.subject ? recordHref(row.subject.model_label, row.subject.record_id) : recordHref(TASK_MODEL, row.id),
    },
  ];
}

function DueTasksLabel(): React.ReactElement {
  const t = useProjectsT();
  return <>{t("calendar.dueTasks")}</>;
}

/** The viewer's open tasks by due date, overlaid on the calendar page (`calendar.events#occurrence-sources`). */
export const dueTasksCalendarSource: CalendarSourceContribution = ({ viewerId, recordHref }) =>
  calendarWindowSource({
    key: "projects.tasks",
    label: <DueTasksLabel />,
    document: CalendarDueTasksDocument,
    variables: (window) => ({
      assignee: viewerId,
      window_start: localDay(window.start),
      window_end: localDay(window.end),
    }),
    select: (data) => data?.project_tasks.flatMap((row) => dueTaskOccurrences(row, recordHref)),
    models: [TASK_MODEL],
    // Signed out, there is no viewer whose tasks to show.
    enabled: viewerId !== "",
  });
