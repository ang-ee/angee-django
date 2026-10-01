// Title: Gantt I18n
// Description: Default UI texts, date-format strings, and formatter functions for the gantt, fully overridable per key.

import type {
  GanttDateRange,
  GanttScale,
} from "./gantt-types"
import {
  format,
  subMilliseconds,
  type Locale,
} from "date-fns"
import { formatDate, formatDateRange } from "../../widgets/date-format"

interface GanttI18nConfig {
  labels: {
    today: string
    previous: string
    next: string
    addEvent: string
    /** "Add task" hint at the foot of the tree. */
    addTask: string
    allDay: string
    loading: string
    event: string
    events: (count: number) => string
    week: (weekNumber: number) => string
    resources: string
    goToDate: string
    /** Hover hint over empty row space, click-only create. */
    scheduleHint: string
    /** Same hint where dragCreate is on and a drag paints a range. */
    scheduleHintDrag: string
    reorder: string
    /** Scale switcher label ("Timeline scale"). */
    selectView: string
    zoomIn: string
    zoomOut: string
    /** Aria-label of the tree/timeline splitter. */
    resizePanel: string
    /** Aria-label of the off-screen bar chips. */
    jumpToBar: (title: string) => string
    /** Read to screen readers as part of the bar label. */
    progress: (percent: number) => string
    /** Live duration readout on the resize indicator. */
    durationDays: (days: number) => string
    /** Appended to the bar aria-label when its segment is clipped by the range. */
    continues: string
    /** Names the planned (baseline) range in the bar tooltip and aria-label. */
    planned: (rangeLabel: string) => string
    /** Read to screen readers on a zero-duration (milestone) bar. */
    milestone: string
    scales: {
      day: string
      week: string
      month: string
      quarter: string
      year: string
    }
  }
  /** date-fns format strings, applied with the gantt `locale`. */
  formats: {
    monthTitle: string
    dayTitle: string
    timeGutter: string
    eventTime: string
  }
  functions: {
    formatTitle: (
      scale: GanttScale,
      ctx: {
        date: Date
        activeRange: GanttDateRange
        visibleRange: GanttDateRange
        locale?: Locale
      }
    ) => string
    formatEventTime: (
      start: Date,
      end: Date,
      allDay: boolean,
      locale?: Locale
    ) => string
    formatDayRange: (range: GanttDateRange, locale?: Locale) => string
    /** Composes the bar's screen-reader label from its localized parts. */
    formatEventAriaLabel: (parts: {
      title: string
      timeLabel: string
      /** Localized milestone clause, from `labels.milestone`. */
      milestoneLabel?: string
      rowTitle?: string
      progressLabel?: string
      /** Localized planned-range clause, from `labels.planned`. */
      plannedLabel?: string
      continues: boolean
    }) => string
  }
}

const DEFAULT_LABELS: GanttI18nConfig["labels"] = {
  today: "Today",
  previous: "Previous",
  next: "Next",
  addEvent: "Add event",
  addTask: "Add task",
  allDay: "All day",
  loading: "Loading events",
  event: "event",
  events: (count) => (count === 1 ? "1 event" : `${count} events`),
  week: (weekNumber) => `W${weekNumber}`,
  resources: "Resources",
  goToDate: "Go to date",
  scheduleHint: "Click to add a schedule",
  scheduleHintDrag: "Click or drag to add a schedule",
  reorder: "Reorder",
  selectView: "Select view",
  zoomIn: "Zoom in",
  zoomOut: "Zoom out",
  resizePanel: "Resize panel",
  jumpToBar: (title) => `Scroll to "${title}"`,
  progress: (percent) => `${percent}% complete`,
  durationDays: (days) => (days === 1 ? "1 day" : `${days} days`),
  continues: "continues",
  planned: (rangeLabel) => `Planned ${rangeLabel}`,
  milestone: "milestone",
  scales: {
    day: "Day",
    week: "Week",
    month: "Month",
    quarter: "Quarter",
    year: "Year",
  },
}

const DEFAULT_FORMATS: GanttI18nConfig["formats"] = {
  monthTitle: "MMMM yyyy",
  dayTitle: "EEEE, MMMM d, yyyy",
  timeGutter: "h a",
  eventTime: "h:mm a",
}

/**
 * Default formatting functions BOUND to a config's labels/formats, so that
 * `formats` overrides flow into the default renderers (a consumer overriding
 * formats.eventTime without replacing formatEventTime still sees it applied).
 */
function makeDefaultGanttFunctions(
  cfg: Pick<GanttI18nConfig, "labels" | "formats">,
  timeZone?: string,
): GanttI18nConfig["functions"] {
  return {
    formatTitle: (scale, { date, activeRange, locale }) => {
      const opts = { locale }
      // Gantt supplies a TZDate for day/month/quarter/year titles, so custom
      // date-fns patterns retain the chart's configured calendar zone.
      if (scale === "day") {
        return format(date, cfg.formats.dayTitle, opts)
      }
      if (scale === "month") {
        return format(date, cfg.formats.monthTitle, opts)
      }
      if (scale === "quarter") {
        return format(date, "QQQ yyyy", opts)
      }
      if (scale === "year") {
        return format(date, "yyyy", opts)
      }
      // The title date is already zoned by Gantt; the week endpoints are raw
      // instants, so the shared formatter receives Gantt's time zone.
      const rangeEnd = subMilliseconds(activeRange.end, 1)
      const start = activeRange.start
      return formatDateRange(start, rangeEnd, { locale, timeZone })
    },
    formatEventTime: (start, end, allDay, locale) => {
      const opts = { locale }
      const dateOptions = { locale, timeZone }
      if (end.getTime() === start.getTime()) {
        // a milestone is an instant, not a range - "9:00 AM - 9:00 AM" reads
        // like a data bug
        return allDay
          ? formatDate(start, dateOptions)
          : `${formatDate(start, dateOptions)}, ${format(start, cfg.formats.eventTime, opts)}`
      }
      if (allDay) {
        // a gantt bar is a DATE RANGE: show it, never a bare "All day".
        // Ends are exclusive midnights, so the last shown day is end - 1ms.
        // The bar passes zoned dates; the shared formatter also receives the
        // configured zone for its current-year and calendar-day decisions.
        const last =
          end.getTime() - 1 >= start.getTime() ? subMilliseconds(end, 1) : start
        if (format(start, "yyyy-MM-dd") === format(last, "yyyy-MM-dd")) {
          return formatDate(start, dateOptions)
        }
        return formatDateRange(start, last, dateOptions)
      }
      const fmt = cfg.formats.eventTime
      // Compare calendar days off the last instant because end is exclusive.
      // A multi-day label is one span with both date-time endpoints, never a
      // date span followed by a separate daily time window.
      const lastInstant =
        end.getTime() - 1 >= start.getTime() ? subMilliseconds(end, 1) : start
      if (format(start, "yyyy-MM-dd") !== format(lastInstant, "yyyy-MM-dd")) {
        return formatDateRange(start, end, {
          ...dateOptions,
          formatEndpoint: (date, label) => `${label}, ${format(date, fmt, opts)}`,
        })
      }
      return `${formatDate(start, dateOptions)}, ${format(start, fmt, opts)} – ${format(end, fmt, opts)}`
    },
    formatDayRange: (range, locale) => {
      const rangeEnd = subMilliseconds(range.end, 1)
      return formatDateRange(range.start, rangeEnd, { locale, timeZone })
    },
    formatEventAriaLabel: ({
      title,
      timeLabel,
      milestoneLabel,
      rowTitle,
      progressLabel,
      plannedLabel,
      continues,
    }) =>
      [
        title,
        timeLabel,
        milestoneLabel,
        rowTitle,
        progressLabel,
        plannedLabel,
        continues ? cfg.labels.continues : undefined,
      ]
        .filter(Boolean)
        .join(", "),
  }
}

const DEFAULT_GANTT_I18N: GanttI18nConfig = {
  labels: DEFAULT_LABELS,
  formats: DEFAULT_FORMATS,
  functions: makeDefaultGanttFunctions({
    labels: DEFAULT_LABELS,
    formats: DEFAULT_FORMATS,
  }),
}

/** Deep-partial override shape: replace individual keys, never sections. */
interface GanttI18nOverrides {
  labels?: Partial<Omit<GanttI18nConfig["labels"], "scales">> & {
    scales?: Partial<GanttI18nConfig["labels"]["scales"]>
  }
  formats?: Partial<GanttI18nConfig["formats"]>
  functions?: Partial<GanttI18nConfig["functions"]>
}

/**
 * Shallow merge per nested object, matching the filters.tsx i18n contract:
 * a partial override replaces individual keys, never whole sections. Default
 * functions are re-bound to the MERGED labels/formats so a `formats` (or
 * `labels.continues`) override reaches the default renderers; explicit
 * `functions` overrides still win.
 */
function mergeGanttI18n(overrides?: GanttI18nOverrides, timeZone?: string): GanttI18nConfig {
  if (!overrides && !timeZone) return DEFAULT_GANTT_I18N
  const labels = {
    ...DEFAULT_LABELS,
    ...overrides?.labels,
    // nested section: replace individual scale names, never the whole set
    scales: {
      ...DEFAULT_LABELS.scales,
      ...overrides?.labels?.scales,
    },
  }
  const formats = { ...DEFAULT_FORMATS, ...overrides?.formats }
  return {
    labels,
    formats,
    functions: {
      ...makeDefaultGanttFunctions({ labels, formats }, timeZone),
      ...overrides?.functions,
    },
  }
}

export { DEFAULT_GANTT_I18N, mergeGanttI18n }
export type { GanttI18nConfig, GanttI18nOverrides }
