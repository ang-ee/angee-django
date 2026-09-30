import { addDays } from "date-fns";
import type { Row } from "@angee/metadata";
import { dateFromUnknown } from "../../widgets/date-format";
import { readPath } from "../resource/resource-view-list-body";
import { toneColorVar, toneOnColorVar, type Tone } from "../../lib/tones";
import type { GanttEvent } from "./GanttView";

/** Convert one scheduled record to the drawing library's exclusive-end event. */
export function ganttBarEvent(row: Row, options: {
  id: string;
  resourceId: string;
  start: string;
  end: string;
  label: string;
  tone: Tone;
  current?: boolean;
  dateOnly: boolean;
}): GanttEvent | null {
  const startValue = readPath(row, options.start);
  const endValue = readPath(row, options.end);
  if (startValue == null || endValue == null) return null;
  const start = dateFromUnknown(startValue);
  const end = dateFromUnknown(endValue);
  if (!start || !end || end < start) return null;
  return {
    id: options.id,
    title: String(readPath(row, options.label) ?? options.id),
    start,
    end: options.dateOnly ? addDays(end, 1) : end,
    allDay: options.dateOnly,
    resourceId: options.resourceId,
    color: toneColorVar(options.tone),
    onColor: toneOnColorVar(options.tone),
    readOnly: true,
    current: options.current,
  };
}
