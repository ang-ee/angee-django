import { lazy, type ReactElement } from "react";

import { LazyBoundary } from "../fragments/LazyBoundary";
import { LoadingPanel } from "../fragments/LoadingPanel";

/**
 * One expanded occurrence, the §3.3 wire shape a server-side occurrence query
 * (`event_occurrences` / `activity_agenda`) returns. `start`/`end` are ISO-8601
 * datetimes; recurrence has already been expanded server-side, so the View
 * renders whatever occurrences it is handed and never re-derives a rule.
 * `editable` gates drag: a recurring master expands to non-editable occurrences,
 * a plain event to an editable one (§3.3).
 */
export interface Occurrence {
  /** Stable synthetic id: `<event_sqid>:<start>` for a recurring occurrence, `<event_sqid>` for a plain one. */
  occurrence_id: string;
  /** The master event's sqid — click/detail paths resolve the master through it. */
  event_sqid: string;
  title: string;
  /** ISO-8601 UTC start. */
  start: string;
  /** ISO-8601 UTC end. */
  end: string;
  all_day: boolean;
  /** `false` whenever the event carries a recurrence — drag is then withheld (§3.3). */
  editable: boolean;
}

export type CalendarViewMode = "month" | "week" | "day";

/** A visible calendar window: `start` inclusive, `end` exclusive (§3.2). */
export interface CalendarWindow {
  start: Date;
  end: Date;
}

export interface CalendarViewProps {
  /** Server-expanded occurrences to render (the §3.3 wire shape). */
  occurrences: readonly Occurrence[];
  /** The controlled calendar mode. */
  view: CalendarViewMode;
  /** The controlled anchor window; the View positions the grid within it. */
  range: CalendarWindow;
  /** Fires when navigation changes the visible window — the caller refetches for it. */
  onRangeChange?: (window: CalendarWindow) => void;
  /**
   * Fires when an editable occurrence is dragged to new bounds. It can never
   * fire for a non-editable occurrence: the View withholds drag for one (§3.3).
   */
  onEventDrop?: (occurrence: Occurrence, start: Date, end: Date | null) => void;
  /**
   * Fires when a time range is selected — the quick-create seam. The caller
   * opens the create dialog seeding start/end via `Field.defaultValue` (F-c);
   * the View owns no create form.
   */
  onSelectRange?: (start: Date, end: Date) => void;
  /** Fires when an occurrence is clicked; the caller resolves the master by `event_sqid`. */
  onEventClick?: (occurrence: Occurrence) => void;
  className?: string;
}

// The FullCalendar bundle is heavy; it is code-split behind a dynamic import so
// it loads only when a calendar mounts, never into the base bundle (§3.1). Every
// `@fullcalendar/*` import lives in the surface module below this boundary.
const CalendarSurface = lazy(() => import("./calendar-surface"));

/**
 * The month/week/day event calendar — a standalone `@angee/ui` primitive
 * (occurrences in, interaction callbacks out, the `TimelineView`/`GraphView`
 * shape) composed over FullCalendar. Recurrence is not its concern: it renders
 * server-expanded occurrences and wires drag/select/click back to the caller.
 * Pair it with {@link useCalendarWindow} for the fetch + window refetch.
 */
export function CalendarView(props: CalendarViewProps): ReactElement {
  return (
    <LazyBoundary pending={<LoadingPanel />}>
      <CalendarSurface {...props} />
    </LazyBoundary>
  );
}
