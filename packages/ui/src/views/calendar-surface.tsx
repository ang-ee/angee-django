import { useEffect, useRef, type ReactElement } from "react";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import type { CalendarViewMode, CalendarViewProps, Occurrence } from "./CalendarView";
import "./calendar-surface.css";

// The code-split half of `CalendarView`: it holds every `@fullcalendar/*`
// import, so the base bundle never pulls the heavy calendar (§3.1). It is thin
// glue — map the §3.3 occurrence wire shape onto FullCalendar events and wire
// its interactions back to the View's callbacks.

const FC_VIEW: Record<CalendarViewMode, string> = {
  month: "dayGridMonth",
  week: "timeGridWeek",
  day: "timeGridDay",
};

const PLUGINS = [dayGridPlugin, timeGridPlugin, interactionPlugin];

export default function CalendarSurface({
  occurrences,
  view,
  range,
  onRangeChange,
  onEventDrop,
  onSelectRange,
  onEventClick,
  className,
}: CalendarViewProps): ReactElement {
  const t = useUiT();
  const fcRef = useRef<FullCalendar>(null);
  // Dedupe emitted windows so a controlled re-render never re-fires the caller.
  const emitted = useRef("");

  // Controlled mode: switch FullCalendar when the caller changes `view`.
  useEffect(() => {
    fcRef.current?.getApi().changeView(FC_VIEW[view]);
  }, [view]);

  // Controlled anchor: reposition only when the requested start falls outside
  // the visible range, so the echo the caller stores from `onRangeChange` never
  // jumps the grid (activeStart can precede `range.start` in a month view).
  useEffect(() => {
    const api = fcRef.current?.getApi();
    if (!api) return;
    const active = api.view;
    if (range.start < active.activeStart || range.start >= active.activeEnd) {
      api.gotoDate(range.start);
    }
  }, [range.start]);

  const events = occurrences.map((occ) => ({
    id: occ.occurrence_id,
    title: occ.title,
    start: occ.start,
    end: occ.end,
    allDay: occ.all_day,
    // FullCalendar withholds drag for a non-editable event, so `onEventDrop`
    // can never fire for a recurring occurrence (§3.3).
    editable: occ.editable,
    extendedProps: { occurrence: occ },
  }));

  return (
    <div className={cn("angee-calendar flex min-h-0 flex-1 flex-col bg-canvas", className)}>
      <FullCalendar
        ref={fcRef}
        plugins={PLUGINS}
        initialView={FC_VIEW[view]}
        initialDate={range.start}
        headerToolbar={{ left: "prev,next today", center: "title", right: "" }}
        buttonText={{ today: t("calendar.today") }}
        height="100%"
        expandRows
        dayMaxEvents
        editable={Boolean(onEventDrop)}
        selectable={Boolean(onSelectRange)}
        selectMirror
        events={events}
        datesSet={(arg) => {
          const key = `${arg.start.getTime()}-${arg.end.getTime()}`;
          if (key === emitted.current) return;
          emitted.current = key;
          onRangeChange?.({ start: arg.start, end: arg.end });
        }}
        select={onSelectRange ? (arg) => onSelectRange(arg.start, arg.end) : undefined}
        eventDrop={
          onEventDrop
            ? (arg) => {
                const occ = arg.event.extendedProps.occurrence as Occurrence | undefined;
                // Belt to FullCalendar's per-event gate: never reschedule a
                // non-editable occurrence even if a drop somehow arrives (§3.3).
                if (!occ || !occ.editable) {
                  arg.revert();
                  return;
                }
                onEventDrop(occ, arg.event.start ?? new Date(occ.start), arg.event.end);
              }
            : undefined
        }
        eventClick={
          onEventClick
            ? (arg) => {
                const occ = arg.event.extendedProps.occurrence as Occurrence | undefined;
                if (occ) onEventClick(occ);
              }
            : undefined
        }
      />
    </div>
  );
}
